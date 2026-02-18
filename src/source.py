import logging
from io import BytesIO
from typing import TYPE_CHECKING, NamedTuple

from msgspec import json
from PIL import Image
from pyfuse3 import ROOT_INODE, InodeT

from .core import IMAGE_EXTENSIONS, new_id, now
from .models import File, Folder, Meta

if TYPE_CHECKING:
    from pathlib import Path

    from src.type import ID
ROOT_ID = "root"
NULL_ID = "null"
NULL_INODE = InodeT(2)


class ImagePath(NamedTuple):
    file: "Path"
    metadata: "Path"
    thumb: "Path"


class EagleLibrarySource:
    """Eagle 素材库数据源。

    负责与 Eagle 素材库交互，建立和维护缓存映射关系。
    提供文件和文件夹的查询接口。
    """

    __slots__ = (
        "_last_check_time",
        "children_map",
        "folder_parent_map",
        "id_map",
        "inode_map",
        "meta",
        "src",
        "void_folder",
    )

    def __init__(self, path: "Path") -> None:
        """初始化数据源。

        Args:
            path: Eagle 素材库的根目录路径。
        """
        self.src = path
        self.void_folder = Folder(id=NULL_ID, name="未分类")
        """虚拟的“未分类”文件夹，用于存放不属于任何文件夹的素材"""
        self.id_map: dict[ID, InodeT] = {ROOT_ID: ROOT_INODE, NULL_ID: NULL_INODE}
        """通过素材ID索引到文件对象的映射"""
        self.children_map: dict[InodeT, dict[str, InodeT]] = {
            ROOT_INODE: {"未分类": NULL_INODE},
            NULL_INODE: {},
        }
        """记录文件夹的子文件夹和文件的映射"""
        self.inode_map: dict[InodeT, File | Folder] = {NULL_INODE: self.void_folder}
        """记录inode ID到素材ID的映射"""
        self.folder_parent_map: dict[InodeT, InodeT] = {NULL_INODE: ROOT_INODE}
        """反向映射，用于从目录找到其上级目录，同样可以当作所有目录的索引"""
        self.meta = self._read_meta()
        """元数据信息"""

        self._init_subfolder(self.meta.folders)
        # endregion

        # 建立文件和ID的映射
        for dir in (self.src / "images").iterdir():
            try:
                image = self._read_image_meta(dir.name[:-5])
            except FileNotFoundError:
                logging.warning(f"文件夹存在 {dir.name} 但没有 metadata，可能是废弃文件")
                continue

            if image.isDeleted:
                continue
            self.id_map[image.id] = image.inode_id
            self.inode_map[image.inode_id] = image
            if image.folders:
                for folder_id in image.folders:
                    folder = self.id_map.get(folder_id)
                    if folder is None:
                        logging.warning(f"文件夹 {folder_id} 不存在，可能是文件夹结构变化")
                        folder = self.id_map[folder_id]
                        self.children_map[NULL_INODE][self.inode_map[folder].fullname] = folder
                        continue
                    self.children_map[folder][image.fullname] = image.inode_id
            else:
                self.children_map[NULL_INODE][image.fullname] = image.inode_id
        self._last_check_time = now()

        # region 初始化文件夹

    def _init_subfolder(
        self, children: list[Folder], parent_inode: InodeT = ROOT_INODE, loop: bool = True
    ) -> None:
        """递归遍历文件夹树，建立映射关系。

        Args:
            parent: 父文件夹对象。
            path: 当前文件夹的 FUSE 路径。
        """
        self.children_map[parent_inode] |= {i.fullname: i.inode_id for i in children}
        for folder in children:
            self.id_map[folder.id] = folder.inode_id
            self.inode_map[folder.inode_id] = folder
            self.children_map[folder.inode_id] = {}
            self.folder_parent_map[folder.inode_id] = parent_inode
            if loop and folder.children:
                self._init_subfolder(folder.children, folder.inode_id)

    # region 图片读取
    def read_image(self, file: File):
        return (self.src / "images" / (file.id + ".info") / f"{file.fullname}").read_bytes()

    def _read_image_meta(self, file_id: "ID"):
        return json.decode(
            (self.src / "images" / (file_id + ".info") / "metadata.json").read_text(
                encoding="utf-8"
            ),
            type=File,
        )

    def _read_image_thumb(self, file_id: "ID"):
        thumb_path = self.src / "images" / (file_id + ".info") / f"{file_id}_thumbnail.png"
        if not thumb_path.exists():
            return b""
        return thumb_path.read_bytes()

    def _read_meta(self):
        return json.decode((self.src / "metadata.json").read_text(encoding="utf-8"), type=Meta)

    def _read_mtime(self):
        return json.decode(
            (self.src / "mtime.json").read_text(encoding="utf-8"), type=dict[str, int]
        )

    def _save_image(self, file: File, data: bytes):
        image_dir = self.src / "images" / (file.id + ".info")
        image_dir.mkdir(parents=True, exist_ok=True)
        (image_dir / f"{file.fullname}").write_bytes(data)
        # 检查是否为支持的图片格式
        if file.ext.lower() not in IMAGE_EXTENSIONS:
            return True

        try:
            # 打开图片并生成缩略图
            with Image.open(BytesIO(data)) as img:
                ori_size = img.size
                scale = min(1, 320 / min(ori_size))
                img.convert("RGB").resize(
                    (int(ori_size[0] * scale), int(ori_size[1] * scale)), Image.Resampling.LANCZOS
                ).save(image_dir / f"{file.id}_thumbnail.png", "PNG", optimize=True)

            return True
        except Exception as e:
            logging.warning(f"生成缩略图失败 {file.id}.info/{file.fullname}: {e}")
            return False

    def _save_image_meta(self, file: File):
        dir = self.src / "images" / (file.id + ".info")
        dir.mkdir(parents=True, exist_ok=True)
        (dir / "metadata.json").write_bytes(json.encode(file))
        mtime = self._read_mtime()
        mtime[file.id] = now()
        (self.src / "mtime.json").write_bytes(json.encode(mtime))

    def _save_meta(self):
        (self.src / "metadata.json").write_bytes(json.encode(self.meta))

    # endregion

    def update_cache(self) -> None:
        """增量更新缓存。

        根据 mtime.json 中的时间戳，仅更新发生变化的素材。
        同时检查 metadata.json 的修改时间，处理文件夹结构变化。
        """
        # region 检查文件夹结构变化
        if now() - self._last_check_time < 1000:  # 1秒
            return

        meta = self._read_meta()
        all_folders = set()

        def _check_folder(folder: Folder, parent_id: InodeT):
            all_folders.add(folder.id)
            if folder.modificationTime > self._last_check_time:
                self.id_map[folder.id] = folder.inode_id
                self.inode_map[folder.inode_id] = folder
                self.folder_parent_map[folder.inode_id] = parent_id
                self.children_map[folder.inode_id] = {}
                self._init_subfolder(folder.children, folder.inode_id, loop=False)

            for child in folder.children:
                _check_folder(child, folder.inode_id)

        if meta.modificationTime > self._last_check_time:
            self._init_subfolder(meta.folders, loop=False)
        for _folder in meta.folders:
            _check_folder(_folder, ROOT_INODE)

        for folder_id in set(self.folder_parent_map.keys()) - all_folders:
            self.folder_parent_map.pop(folder_id)
            self.inode_map.pop(folder_id)
            self.children_map.pop(folder_id)

        self.meta = meta
        # endregion

        # 检查文件变化
        for k, v in self._read_mtime().items():
            if v > self._last_check_time:
                new_file = self._read_image_meta(k)
                f_inode = self.id_map[k]
                f_name = self.inode_map[f_inode].fullname
                # 先从目录中删除
                for folder in new_file.folders:
                    self.children_map[self.id_map[folder]].pop(f_name)
                # 如果已删除，从映射中移除
                if new_file.isDeleted:
                    self.inode_map.pop(f_inode, None)
                    self.id_map.pop(k, None)
                    continue
                # 更新文件映射
                self.id_map[k] = new_file.inode_id
                self.inode_map[f_inode] = new_file
                # 更新目录
                for folder in new_file.folders:
                    self.children_map[self.id_map[folder]][new_file.fullname] = new_file.inode_id
        self._last_check_time = now()

    # ==================== 文件操作方法 ====================

    def new_file(self, folder: InodeT, name: str, data: bytes, ext: str) -> bool:
        if f"{name}.{ext}" in self.children_map[folder]:
            return False
        size = Image.open(BytesIO(data)).size
        file = File(
            id=new_id(),
            name=name,
            size=len(data),
            btime=now(),
            mtime=now(),
            ext=ext,
            tags=[],
            folders=[self.inode_map[folder].id],
            isDeleted=False,
            url="",
            annotation="",
            modificationTime=now(),
            height=size[1],
            width=size[0],
            lastModified=now(),
            palettes=[],
        )
        self.id_map[file.id] = file.inode_id
        self.inode_map[file.inode_id] = file
        self.children_map[folder][file.fullname] = file.inode_id
        self._save_image(file, data)
        self._save_image_meta(file)
        return True

    def write_file(self, file_inode: InodeT, data: bytes):
        file = self.inode_map[file_inode]
        if isinstance(file, Folder):
            return
        file.size = len(data)
        file.mtime = now()
        file.modificationTime = now()
        file.lastModified = now()
        self._save_image(file, data)
        self._save_image_meta(file)

    def new_folder(self, parent: InodeT, name: str) -> bool:
        if name in self.children_map[parent]:
            return False
        parent_f = self.inode_map[parent]
        if isinstance(parent_f, File):
            return False
        folder = Folder(
            id=new_id(),
            name=name,
            modificationTime=now(),
        )
        self.id_map[folder.id] = folder.inode_id
        self.inode_map[folder.inode_id] = folder
        self.folder_parent_map[folder.inode_id] = parent
        self.children_map[parent][folder.fullname] = folder.inode_id
        parent_f.children.append(folder)
        self.children_map[folder.inode_id] = {}
        self._save_meta()
        return True

    def delete_node(self, inode: InodeT) -> None:
        """删除素材"""
        file = self.inode_map.pop(inode)
        self.id_map.pop(file.id, None)
        if isinstance(file, File):
            for folder in file.folders:
                self.children_map[self.id_map[folder]].pop(file.fullname)
            file.isDeleted = True
            self._save_image_meta(file)
        else:
            # 递归删除子文件夹及文件
            file.children.clear()
            for child in file.children:
                self.delete_node(child.inode_id)
            parent = self.folder_parent_map.pop(inode)
            self.children_map[parent].pop(file.fullname)
            self._save_meta()

    def rename_node(
        self, oldname: str, newname: str, new_parent_inode: InodeT, old_parent_inode: InodeT
    ) -> None:
        """重命名或移动素材。"""
        inode = self.children_map[old_parent_inode][oldname]
        file = self.inode_map[inode]
        if isinstance(file, File):
            file.name, file.ext = newname.rsplit(".", 1)
            file.modificationTime = now()
            if new_parent_inode != old_parent_inode:
                self.children_map[new_parent_inode][file.fullname] = file.inode_id
                self.children_map[old_parent_inode].pop(file.fullname)

            self._save_image_meta(file)
        else:
            file.name = newname
            file.modificationTime = now()
            if new_parent_inode != old_parent_inode:
                self.children_map[new_parent_inode][file.fullname] = file.inode_id
                self.children_map[old_parent_inode].pop(file.fullname)
                old_folder = self.inode_map[old_parent_inode]
                if isinstance(old_folder, File):
                    raise TypeError
                old_folder.children.remove(file)
                new_folder = self.inode_map[new_parent_inode]
                if isinstance(new_folder, File):
                    raise TypeError
                new_folder.children.append(file)
                self.folder_parent_map[file.inode_id] = new_parent_inode
            self._save_meta()
