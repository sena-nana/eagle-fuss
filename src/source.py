import logging
import random
import string
from pathlib import Path
from typing import TYPE_CHECKING

from msgspec import json
from PIL import Image

from .core import IMAGE_EXTENSIONS, now
from .models import File, Folder, Meta

if TYPE_CHECKING:
    from src.type import ID


class EagleLibrarySource:
    """Eagle 素材库数据源。

    负责与 Eagle 素材库交互，建立和维护缓存映射关系。
    提供文件和文件夹的查询接口。

    核心映射关系：
        - id_map: ID -> File（素材 ID 到文件对象的映射）
        - dir_map: ID -> Folder（文件夹 ID 到文件夹对象的映射）
        - dir_file_map: ID -> {name: File}（文件夹 ID 到其包含文件的映射）
        - path_dir_map: Path -> Folder（FUSE 路径到文件夹对象的映射）

    Attributes:
        src: Eagle 素材库的源路径。
        id_map: 通过 ID 索引到文件对象的映射，存储所有文件对象的原始引用。
        update_time: 上次缓存更新的时间戳（毫秒）。
        dir_map: 从 ID 到文件夹对象的映射，原始引用存在父文件夹的 children 中。
        dir_file_map: 从文件夹 ID 到文件对象列表的映射。
        path_dir_map: 从路径到文件夹对象的映射。
        void_folder: 虚拟的"未分类"文件夹，用于存放不属于任何文件夹的素材。
        meta: 素材库的主元数据对象。
        root: 虚拟根文件夹对象。
        _last_check_time: 上次检查增量更新的时间戳（毫秒），用于1秒节流。

    Example:
        ```python
        from pathlib import Path
        from src.source import EagleLibrarySource

        source = EagleLibrarySource(Path("test.library"))
        file = source.get_file("/文件夹名/素材名.pdf")
        folder = source.get_folder("/文件夹名")
        ```
    """

    def __init__(self, path: Path) -> None:
        """初始化数据源。

        Args:
            path: Eagle 素材库的根目录路径。
        """
        self.src = path
        self.id_map: dict[ID, File] = {}
        """通过ID索引到文件对象的映射，此处存储所有文件对象原始引用"""
        self.update_time = 0
        self.dir_map: dict[ID, Folder] = {}
        """从ID到文件夹对象的映射，原始引用存在父文件夹的children中"""
        self.dir_file_map: dict[ID, dict[str, File]] = {}
        """从文件夹ID到文件对象列表的映射"""
        self.path_dir_map: dict[Path, Folder] = {}
        """从路径到文件夹对象的映射"""
        self._last_check_time = 0
        """上次检查增量更新的时间戳（毫秒），用于1秒节流"""

        # 支持的图片格式（用于生成缩略图）
        self.image_extensions = IMAGE_EXTENSIONS

        self._init_cache()

    def _init_cache(self) -> None:
        """初始化缓存，建立所有映射关系。

        执行以下操作：
            1. 创建"未分类"虚拟文件夹。
            2. 加载素材库主元数据（metadata.json）。
            3. 建立文件夹的 ID 和路径映射。
            4. 遍历所有素材，建立文件映射。
            5. 更新缓存时间戳。
        """
        # 建立文件夹和ID的映射
        self.void_folder = Folder(id="null", name="未分类")
        self.meta = json.decode((self.src / "metadata.json").read_text(encoding="utf-8"), type=Meta)
        self.root = Folder(children_=[*self.meta.folders, self.void_folder])
        self.path_dir_map[Path("/")] = self.root

        def _loop_folders(parent: Folder, path: Path) -> None:
            """递归遍历文件夹树，建立映射关系。

            Args:
                parent: 父文件夹对象。
                path: 当前文件夹的 FUSE 路径。
            """
            for folder in parent.children_:
                sub_path = Folder(folder.id, folder.name)
                self.dir_map[folder.id] = sub_path
                self.path_dir_map[path / folder.name] = sub_path
                if folder.children_:
                    _loop_folders(folder, path / folder.name)

        _loop_folders(self.root, Path("/"))
        # 建立文件和ID的映射
        for dir in (self.src / "images").iterdir():
            image_meta = self._load_file(dir.stem)
            if image_meta.isDeleted:
                continue
            self.id_map[image_meta.id] = image_meta
            if image_meta.folders:
                for folder_id in image_meta.folders:
                    self.dir_file_map.setdefault(folder_id, {})[image_meta.name] = image_meta
            else:
                self.dir_file_map.setdefault(self.void_folder.id, {})[image_meta.name] = image_meta
        self.update_time = now()

    def _update_cache(self) -> None:
        """增量更新缓存。

        根据 mtime.json 中的时间戳，仅更新发生变化的素材。
        同时检查 metadata.json 的修改时间，处理文件夹结构变化。

        处理以下情况：
            - 素材被删除：从映射中移除。
            - 素材所属文件夹变更：更新文件夹映射。
            - 素材内容变更：更新文件对象。
            - 文件夹结构变化：重新加载文件夹映射。
        """
        # 检查文件夹结构变化
        metadata_path = self.src / "metadata.json"
        if metadata_path.exists():
            meta = json.decode(metadata_path.read_text(encoding="utf-8"), type=Meta)
            if meta.modificationTime > self.meta.modificationTime:
                # 文件夹结构发生变化，需要重新初始化缓存
                self._init_cache()
                return

        # 检查文件变化
        mtime_path = self.src / "mtime.json"
        if not mtime_path.exists():
            self.update_time = now()
            return

        mtime = json.decode(mtime_path.read_text(encoding="utf-8"), type=dict[str, int])
        for k, v in mtime.items():
            if v > self.update_time:
                new_file = self._load_file(k)
                if new_file.isDeleted:
                    # 修复：检查folders列表是否为空
                    if new_file.folders:
                        self.dir_file_map.get(new_file.folders[0], {}).pop(
                            self.id_map[k].name, None
                        )
                    else:
                        self.dir_file_map.get(self.void_folder.id, {}).pop(
                            self.id_map[k].name, None
                        )
                    del self.id_map[k]
                    continue
                if new_file.folders != self.id_map[k].folders:
                    # 文件夹变更，更新映射
                    old_folders = self.id_map[k].folders
                    for old_folder in old_folders:
                        self.dir_file_map.get(old_folder, {}).pop(self.id_map[k].name, None)
                    for new_folder in new_file.folders:
                        self.dir_file_map.setdefault(new_folder, {})[new_file.name] = new_file
                self.id_map[k] = new_file
        self.update_time = now()

    def check_and_update_cache(self) -> None:
        """检查并更新缓存（带1秒节流）。

        如果距离上次检查超过1秒，则执行增量更新。
        否则跳过检查。
        """
        current_time = now()
        if current_time - self._last_check_time < 1000:  # 1秒
            return

        self._update_cache()
        self._last_check_time = current_time

    def get_file(self, path: str) -> File | None:
        """根据 FUSE 路径获取文件对象。

        映射流程：
            1. 解析路径，获取父目录路径和文件名。
            2. 通过 path_dir_map 获取父文件夹对象。
            3. 通过 folder.subfiles 获取文件对象。

        Args:
            path: FUSE 文件系统中的文件路径，如 "/文件夹名/素材名.pdf"。

        Returns:
            找到的 File 对象，未找到则返回 None。
        """
        path_ = Path(path)
        parent_path = path_.parent
        folder = self.path_dir_map.get(parent_path)
        if not folder:
            return None
        return self.dir_file_map.get(folder.id, {}).get(path_.name)

    def get_folder(self, path: str) -> Folder | None:
        """根据 FUSE 路径获取文件夹对象。

        Args:
            path: FUSE 文件系统中的目录路径，如 "/文件夹名"。

        Returns:
            找到的 Folder 对象，未找到则返回 None。
        """
        return self.path_dir_map.get(Path(path))

    def _load_file(self, file_id: "ID") -> File:
        """加载单个素材的元数据。

        Args:
            file_id: 素材的唯一标识符。

        Returns:
            解析后的 File 对象。
        """
        return json.decode(
            (self.src / "images" / file_id / "metadata.json").read_text(encoding="utf-8"), type=File
        )

    # ==================== 辅助方法：ID 生成与元数据保存 ====================

    def _generate_id(self) -> "ID":
        """生成新的素材/文件夹 ID。

        Eagle ID 格式：13 位大写字母和数字组合。
        使用时间戳和随机数生成，确保唯一性。

        Returns:
            新生成的唯一 ID。
        """

        chars = string.ascii_uppercase + string.digits
        return "".join(random.choices(chars, k=13))

    def _save_file_metadata(self, file: File) -> None:
        """保存素材元数据到文件系统。

        将 File 对象序列化为 JSON 并写入 images/{id}/metadata.json。

        Args:
            file: 要保存的文件对象。
        """
        file_dir = self.src / "images" / file.id
        file_dir.mkdir(parents=True, exist_ok=True)
        (file_dir / "metadata.json").write_bytes(json.encode(file))

    def _update_mtime(self, file_id: "ID") -> None:
        """更新素材的修改时间戳。

        更新 mtime.json 中对应素材的时间戳，用于增量同步。

        Args:
            file_id: 素材 ID。
        """
        mtime_path = self.src / "mtime.json"
        mtime: dict[str, int] = {}
        if mtime_path.exists():
            mtime = json.decode(mtime_path.read_text(encoding="utf-8"), type=dict[str, int])
        mtime[file_id] = now()
        mtime_path.write_bytes(json.encode(mtime))

    def _save_library_metadata(self) -> None:
        """保存素材库主元数据。

        将当前的文件夹结构序列化并写入 metadata.json。
        """
        # 更新 meta 对象的 modificationTime
        self.meta.modificationTime = now()
        self.meta.folders = sorted(self.meta.folders, key=lambda f: f.name)
        (self.src / "metadata.json").write_bytes(json.encode(self.meta))

    def _create_image_dir(self, file_id: "ID") -> Path:
        """创建素材目录。

        创建 images/{id}/ 目录。

        Args:
            file_id: 素材 ID。

        Returns:
            创建的目录路径。
        """
        dir_path = self.src / "images" / file_id
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    # ==================== 缓存操作方法 ====================

    def add_file_to_cache(self, file: File) -> None:
        """将文件添加到缓存映射。

        更新 id_map 和 dir_file_map。

        Args:
            file: 要添加的文件对象。
        """
        self.id_map[file.id] = file
        if file.folders:
            for folder_id in file.folders:
                self.dir_file_map.setdefault(folder_id, {})[file.name] = file
        else:
            self.dir_file_map.setdefault(self.void_folder.id, {})[file.name] = file

    def remove_file_from_cache(self, file_id: "ID") -> None:
        """从缓存映射中移除文件。

        从 id_map 和 dir_file_map 中移除。

        Args:
            file_id: 要移除的素材 ID。
        """
        if file_id not in self.id_map:
            return
        file = self.id_map[file_id]
        # 从 dir_file_map 中移除
        if file.folders:
            for folder_id in file.folders:
                if folder_id in self.dir_file_map:
                    self.dir_file_map[folder_id].pop(file.name, None)
        else:
            self.dir_file_map.get(self.void_folder.id, {}).pop(file.name, None)
        # 从 id_map 中移除
        del self.id_map[file_id]

    def add_folder_to_cache(self, folder: Folder, parent_path: Path) -> None:
        """将文件夹添加到缓存映射。

        更新 dir_map 和 path_dir_map。

        Args:
            folder: 要添加的文件夹对象。
            parent_path: 父文件夹的 FUSE 路径。
        """
        self.dir_map[folder.id] = folder
        self.path_dir_map[parent_path / folder.name] = folder

    def remove_folder_from_cache(self, folder_id: "ID") -> None:
        """从缓存映射中移除文件夹。

        从 dir_map 和 path_dir_map 中移除。

        Args:
            folder_id: 要移除的文件夹 ID。
        """
        if folder_id not in self.dir_map:
            return
        # 从 path_dir_map 中移除（需要遍历查找）
        paths_to_remove = [p for p, f in self.path_dir_map.items() if f.id == folder_id]
        for path in paths_to_remove:
            del self.path_dir_map[path]
        # 从 dir_map 中移除
        del self.dir_map[folder_id]

    def _generate_thumbnail(self, file_id: "ID", file_name: str, file_ext: str) -> bool:
        """为图片文件生成缩略图。

        仅对支持的图片格式生成缩略图，缩略图保存为 PNG 格式，尺寸为 256x256。

        Args:
            file_id: 素材 ID。
            file_name: 文件名（不含扩展名）。
            file_ext: 文件扩展名。

        Returns:
            是否成功生成缩略图。
        """
        # 检查是否为支持的图片格式
        if file_ext.lower() not in self.image_extensions:
            return False

        try:
            # 构建源文件路径和缩略图路径
            source_path = self.src / "images" / file_id / f"{file_name}.{file_ext}"
            thumbnail_path = self.src / "images" / file_id / f"{file_name}_thumbnail.png"

            # 打开图片并生成缩略图
            with Image.open(source_path) as img:
                # 转换为 RGB 模式（如果是 RGBA 或 P 模式）
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")  # noqa: PLW2901

                # 计算缩略图尺寸，保持宽高比
                img.thumbnail((256, 256), Image.Resampling.LANCZOS)

                # 保存为 PNG 格式
                img.save(thumbnail_path, "PNG", optimize=True)

            return True
        except Exception as e:
            logging.warning(f"生成缩略图失败 {file_id}/{file_name}.{file_ext}: {e}")
            return False

    # ==================== 文件操作方法 ====================

    def create_file(self, path: str, mode: int) -> File:
        """创建新素材。

        Args:
            path: FUSE 路径，如 "/文件夹名/新素材.pdf"。
            mode: 文件权限模式。

        Returns:
            新创建的 File 对象。
        """
        path_ = Path(path)
        parent_path = path_.parent
        file_name = path_.stem
        file_ext = path_.suffix.lstrip(".") or "bin"

        # 获取父文件夹
        parent_folder = self.path_dir_map.get(parent_path)
        folder_ids = [parent_folder.id] if parent_folder and parent_folder.id != "null" else []

        # 生成新 ID
        new_id = self._generate_id()

        # 创建素材目录
        self._create_image_dir(new_id)

        # 创建 File 对象
        current_time = now()
        new_file = File(
            id=new_id,
            name=file_name,
            size=0,
            btime=current_time,
            mtime=current_time,
            ext=file_ext,
            tags=[],
            folders=folder_ids,
            isDeleted=False,
            url="",
            annotation="",
            modificationTime=current_time,
            height=0,
            width=0,
            lastModified=current_time,
            palettes=[],
        )

        # 保存元数据
        self._save_file_metadata(new_file)
        self._update_mtime(new_id)

        # 添加到缓存
        self.add_file_to_cache(new_file)

        # 如果是图片格式，生成缩略图
        if file_ext.lower() in self.image_extensions:
            # 创建空文件以便生成缩略图（实际内容将在 write_file 中写入）
            file_path = self.src / "images" / new_id / f"{file_name}.{file_ext}"
            file_path.touch()
            # 尝试生成缩略图（如果文件有内容）
            self._generate_thumbnail(new_id, file_name, file_ext)

        return new_file

    def write_file(self, file_id: "ID", data: bytes, offset: int) -> int:
        """写入素材文件内容。

        Args:
            file_id: 素材 ID。
            data: 要写入的数据。
            offset: 写入偏移量。

        Returns:
            实际写入的字节数。
        """
        if file_id not in self.id_map:
            return 0

        file = self.id_map[file_id]
        file_path = self.src / "images" / file_id / f"{file.name}.{file.ext}"

        # 写入数据
        with Path(file_path).open("r+b" if file_path.exists() else "wb") as f:
            f.seek(offset)
            written = f.write(data)

        # 更新文件大小和时间戳
        new_size = file_path.stat().st_size
        current_time = now()
        file.size = new_size
        file.mtime = current_time
        file.modificationTime = current_time
        file.lastModified = current_time

        # 保存元数据并更新缓存
        self._save_file_metadata(file)
        self._update_mtime(file_id)

        # 如果是图片格式，生成或更新缩略图
        if file.ext.lower() in self.image_extensions:
            self._generate_thumbnail(file_id, file.name, file.ext)

        return written

    def delete_file(self, path: str) -> None:
        """标记素材为已删除。

        Args:
            path: 文件的 FUSE 路径。
        """
        file = self.get_file(path)
        if file is None:
            return

        # 直接修改文件对象
        current_time = now()
        file.isDeleted = True
        file.mtime = current_time
        file.modificationTime = current_time
        file.lastModified = current_time

        # 保存元数据
        self._save_file_metadata(file)
        self._update_mtime(file.id)

        # 从缓存移除
        self.remove_file_from_cache(file.id)

    def rename_file(self, old_path: str, new_path: str) -> None:
        """重命名或移动素材。

        Args:
            old_path: 原 FUSE 路径。
            new_path: 新 FUSE 路径。
        """
        file = self.get_file(old_path)
        if file is None:
            return

        new_path_ = Path(new_path)
        new_name = new_path_.stem
        new_ext = new_path_.suffix.lstrip(".") or file.ext
        new_parent_path = new_path_.parent

        # 获取新父文件夹
        new_parent = self.path_dir_map.get(new_parent_path)
        new_folder_ids = [new_parent.id] if new_parent and new_parent.id != "null" else []

        # 重命名实际文件
        old_file_path = self.src / "images" / file.id / f"{file.name}.{file.ext}"
        new_file_path = self.src / "images" / file.id / f"{new_name}.{new_ext}"
        if old_file_path.exists():
            old_file_path.rename(new_file_path)

        # 重命名缩略图（如果存在）
        old_thumb_path = self.src / "images" / file.id / f"{file.name}_thumbnail.png"
        new_thumb_path = self.src / "images" / file.id / f"{new_name}_thumbnail.png"
        if old_thumb_path.exists():
            old_thumb_path.rename(new_thumb_path)

        # 从旧缓存位置移除
        self.remove_file_from_cache(file.id)

        # 直接修改文件对象
        current_time = now()
        file.name = new_name
        file.ext = new_ext
        file.folders = new_folder_ids
        file.mtime = current_time
        file.modificationTime = current_time
        file.lastModified = current_time

        # 保存元数据
        self._save_file_metadata(file)
        self._update_mtime(file.id)

        # 添加到新缓存位置
        self.add_file_to_cache(file)

    def update_file_time(self, path: str, times: tuple | None) -> None:
        """更新素材时间戳。

        Args:
            path: 文件的 FUSE 路径。
            times: (atime, mtime) 时间元组。
        """
        file = self.get_file(path)
        if file is None:
            return

        current_time = now()
        if times:
            new_mtime = int(times[1] * 1000)
            file.mtime = new_mtime
        file.modificationTime = current_time
        file.lastModified = current_time

        self._save_file_metadata(file)
        self._update_mtime(file.id)

    def truncate_file(self, file_id: "ID", length: int) -> None:
        """截断素材文件。

        Args:
            file_id: 素材 ID。
            length: 目标长度。
        """
        if file_id not in self.id_map:
            return

        file = self.id_map[file_id]
        file_path = self.src / "images" / file_id / f"{file.name}.{file.ext}"

        # 截断文件
        with Path(file_path).open("r+b" if file_path.exists() else "wb") as f:
            f.truncate(length)

        # 更新文件对象
        current_time = now()
        file.size = length
        file.mtime = current_time
        file.modificationTime = current_time
        file.lastModified = current_time

        self._save_file_metadata(file)
        self._update_mtime(file_id)

    # ==================== 文件夹操作方法 ====================

    def create_folder(self, path: str) -> Folder:
        """创建新文件夹。

        Args:
            path: 新文件夹的 FUSE 路径。

        Returns:
            新创建的 Folder 对象。
        """
        path_ = Path(path)
        parent_path = path_.parent
        folder_name = path_.name

        # 获取父文件夹
        parent_folder = self.path_dir_map.get(parent_path)
        if parent_folder is None:
            raise ValueError(f"Parent folder not found: {parent_path}")

        # 生成新 ID
        new_id = self._generate_id()

        # 创建 Folder 对象
        current_time = now()
        new_folder = Folder(
            id=new_id,
            name=folder_name,
            description="",
            modificationTime=current_time,
            tags=[],
            children_=[],
        )

        # 直接添加到父文件夹的 children_ 列表
        parent_folder.children_.append(new_folder)
        parent_folder.modificationTime = now()

        # 保存元数据
        self._save_library_metadata()

        # 添加到缓存
        self.add_folder_to_cache(new_folder, parent_path)

        return new_folder

    def delete_folder(self, path: str) -> None:
        """删除空文件夹。

        Args:
            path: 文件夹的 FUSE 路径。

        Raises:
            OSError: 文件夹非空或不存在。
        """
        folder = self.get_folder(path)
        if folder is None:
            raise OSError(f"Folder not found: {path}")

        # 检查是否为空
        if folder.children_:
            raise OSError("Directory not empty")
        if self.dir_file_map.get(folder.id):
            raise OSError("Directory not empty")

        # 从父文件夹的 children 中移除
        self._remove_child_from_parent(folder.id)

        # 保存元数据
        self._save_library_metadata()

        # 从缓存移除
        self.remove_folder_from_cache(folder.id)

    def _remove_child_from_parent(self, folder_id: str) -> None:
        """从父文件夹中移除子文件夹。

        Args:
            folder_id: 要移除的文件夹 ID。
        """
        for f in self.meta.folders:
            if self._remove_child_recursive(f, folder_id):
                break

    def _remove_child_recursive(self, folder: Folder, folder_id: str) -> bool:
        """递归查找并移除子文件夹。

        Args:
            folder: 文件夹对象。
            folder_id: 要移除的文件夹 ID。

        Returns:
            是否成功移除。
        """
        for i, child in enumerate(folder.children_):
            if child.id == folder_id:
                folder.children_.pop(i)
                return True
            if self._remove_child_recursive(child, folder_id):
                return True
        return False

    def rename_folder(self, old_path: str, new_path: str) -> None:
        """重命名文件夹。

        Args:
            old_path: 原 FUSE 路径。
            new_path: 新 FUSE 路径。
        """
        folder = self.get_folder(old_path)
        if folder is None:
            return

        new_name = Path(new_path).name

        # 更新文件夹名称
        self._update_folder_name(folder.id, new_name)

        # 保存元数据
        self._save_library_metadata()

        # 更新缓存
        old_path_key = Path(old_path)
        if old_path_key in self.path_dir_map:
            # 直接修改现有文件夹对象的名称
            folder.name = new_name
            folder.modificationTime = now()
            # 更新映射
            del self.path_dir_map[old_path_key]
            self.path_dir_map[Path(new_path)] = folder
            # dir_map 中已经是同一个对象，无需更新

    def _update_folder_name(self, folder_id: str, new_name: str) -> None:
        """更新文件夹名称。

        Args:
            folder_id: 文件夹 ID。
            new_name: 新名称。
        """
        for f in self.meta.folders:
            if self._update_folder_name_recursive(f, folder_id, new_name):
                break

    def _update_folder_name_recursive(self, folder: Folder, folder_id: str, new_name: str) -> bool:
        """递归查找并更新文件夹名称。

        Args:
            folder: 文件夹对象。
            folder_id: 文件夹 ID。
            new_name: 新名称。

        Returns:
            是否成功更新。
        """
        if folder.id == folder_id:
            folder.name = new_name
            folder.modificationTime = now()
            return True
        for child in folder.children_:
            if self._update_folder_name_recursive(child, folder_id, new_name):
                return True
        return False
