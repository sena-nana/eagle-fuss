import logging
from typing import TYPE_CHECKING

from .core import now
from .library import ROOT_ID, VOID_ID, FolderSource, ImageSource, Source

if TYPE_CHECKING:
    from pathlib import Path

    from .models import Folder
    from .type import ID


class EagleLibrarySource:
    """Eagle 素材库数据源。

    负责与 Eagle 素材库交互，建立和维护缓存映射关系。
    提供文件和文件夹的查询接口。
    """

    __slots__ = (
        "_last_check_time",
        "file_id_map",
        "folder_id_map",
        "path_to_id",
        "src",
    )

    def __init__(self, path: "Path") -> None:
        """初始化数据源。

        Args:
            path: Eagle 素材库的根目录路径。
        """
        self.src = Source.load(path)
        root, void = self.src.create_root()

        self.file_id_map: dict[ID, ImageSource] = {}
        """通过ID查找文件的映射"""
        self.folder_id_map: dict[ID, FolderSource] = {}
        """通过ID查找文件夹的映射"""
        self.path_to_id: dict[Path, ID] = {void.target: VOID_ID, root.target: ROOT_ID}
        """通过文件或文件夹路径查找ID的映射"""

        # 子文件夹和子文件直接去对象里找

        """元数据信息"""
        # region 初始化文件
        self._init_subfolder(root)
        void = self.folder_id_map[VOID_ID]  # 重新绑定到已注册的实例，与 folder_id_map 保持一致

        def _init_image(parent: FolderSource, image: ImageSource):
            name = image.meta.fullname
            if name in parent.files or name in parent.subfolders:
                stem, ext = image.meta.name, image.meta.ext
                counter = 2
                while name in parent.files or name in parent.subfolders:
                    name = f"{stem} ({counter}).{ext}"
                    counter += 1
            parent.files[name] = image
            self.file_id_map[image.meta.id] = image
            target = parent.target / name
            image.targets.add(target)
            self.path_to_id[target] = image.meta.id

        # 建立文件和ID的映射
        for dir in (self.src.path / "images").iterdir():
            try:
                image = self.src.image(dir.name[:-5])
            except FileNotFoundError:
                logging.warning(f"文件夹存在 {dir.name} 但没有 metadata，可能是废弃文件")
                continue

            if image.meta.isDeleted:
                continue

            if image.meta.folders:
                for folder_id in image.meta.folders:
                    _folder = self.folder_id_map.get(folder_id)
                    if _folder is None:
                        logging.warning(f"文件夹 {folder_id} 不存在，可能是文件夹结构变化")
                        _init_image(void, image)
                        continue
                    _init_image(_folder, image)
            else:
                _init_image(void, image)
        self._last_check_time = now()
        # endregion

    def _init_subfolder(self, folder: FolderSource, loop: bool = True) -> None:
        """递归遍历文件夹树，建立映射关系。"""
        self.folder_id_map[folder.meta.id] = folder
        self.path_to_id[folder.target] = folder.meta.id
        if loop:
            for child in folder.meta.children:
                subfolder = folder / child
                folder.subfolders[child.fullname] = subfolder
                self._init_subfolder(subfolder)

    def update_cache(self) -> None:
        """增量更新缓存。

        根据 mtime.json 中的时间戳，仅更新发生变化的素材。
        同时检查 metadata.json 的修改时间，处理文件夹结构变化。
        """
        # region 检查文件夹结构变化
        if now() - self._last_check_time < 1000:  # 1秒
            return

        meta = self.src.read_meta()
        all_folders = set(self.folder_id_map.keys())
        all_folders.discard(VOID_ID)
        all_folders.discard(ROOT_ID)

        def _check_folder(folder: "Folder", parent: FolderSource):
            all_folders.discard(folder.id)
            if folder.modificationTime > self._last_check_time:
                new = parent / folder
                if folder.id in self.folder_id_map:
                    new.files.update(self.folder_id_map[folder.id].files)
                self._init_subfolder(new, loop=False)
            else:
                new = self.folder_id_map[folder.id]

            for child in folder.children:
                _check_folder(child, new)

        root = self.folder_id_map[ROOT_ID]
        for _folder in meta.folders:
            _check_folder(_folder, root)

        for folder_id in all_folders:
            folder = self.folder_id_map.pop(folder_id)
            self.path_to_id.pop(folder.target)

        # endregion
        # 检查文件变化
        for k, v in self.src.read_mtime().items():
            if v > self._last_check_time:
                if k in self.file_id_map:
                    old_file = self.file_id_map.pop(k)
                    # 先从目录中删除
                    for folder in old_file.meta.folders:
                        if folder in self.folder_id_map and old_file.meta.fullname in self.folder_id_map[folder].files:
                            self.folder_id_map[folder].files.pop(old_file.meta.fullname)
                # 更新文件映射
                new_file = self.src.image(k)
                if new_file.meta.isDeleted:
                    continue
                self.file_id_map[k] = new_file
                for folder in new_file.meta.folders:
                    self.folder_id_map[folder].add_file(new_file)
        self._last_check_time = now()

    # ==================== 文件操作方法 ====================

    def new_file(self, path: "Path", data: bytes) -> bool:
        if path in self.path_to_id:
            return False
        folder = path.parent
        if folder not in self.path_to_id:
            return False
        if (image := self.folder_id_map[self.path_to_id[folder]].new_file(data, path.stem, path.suffix.lstrip("."))) is None:
            return False
        self.file_id_map[image.meta.id] = image
        self.path_to_id[path] = image.meta.id
        return True

    def write_file(self, path: "Path", data: bytes):
        if (_id := self.path_to_id.get(path)) is None:
            return False
        file = self.file_id_map[_id]
        file.meta.size = len(data)
        time = now()
        file.meta.mtime = time
        file.meta.modificationTime = time
        file.meta.lastModified = time
        file.save_data(data)
        return True

    def new_folder(self, path: "Path") -> bool:
        parent, name = path.parent, path.name
        if parent not in self.path_to_id:
            return False
        parent_id = self.path_to_id[parent]
        parent_folder = self.folder_id_map[parent_id]
        if not (subfolder := parent_folder.new_subfolder(name)):
            return False
        self.folder_id_map[subfolder.meta.id] = subfolder
        self.path_to_id[path] = subfolder.meta.id
        if parent_id == ROOT_ID:
            self.src.meta.folders.append(subfolder.meta)
        self.src.save_meta()
        return True

    def delete_node(self, path: "Path") -> bool:
        """删除素材"""
        if path not in self.path_to_id:
            return False
        _id = self.path_to_id.pop(path)
        if _id in self.file_id_map:
            image = self.file_id_map.pop(_id)
            image.delete()
            parent, name = path.parent, path.name
            folder = self.folder_id_map[self.path_to_id[parent]]
            folder.files.pop(name)
            return True
        if _id in self.folder_id_map:
            subfolder = self.folder_id_map.pop(_id)

            def clear_images(folder: FolderSource):
                for image in folder.files.values():
                    target = folder.target / image.meta.fullname
                    image.meta.folders.remove(folder.meta.id)
                    image.targets.remove(target)
                    self.path_to_id.pop(target)
                    if not image.meta.folders:
                        self.file_id_map.pop(image.meta.id)
                        image.delete()
                    else:
                        image.save_meta()

                for subfolder in folder.subfolders.values():
                    clear_images(subfolder)

            clear_images(subfolder)
            parent, name = path.parent, path.name
            folder = self.folder_id_map[self.path_to_id[parent]]
            folder.subfolders.pop(name)
            self.src.save_meta()
            return True
        return False

    def _repath_folder(self, folder: "FolderSource", new_target: "Path") -> None:
        """递归更新文件夹及其所有子项在 path_to_id 和 targets 中的路径（原地修改）。"""
        old_target = folder.target

        # 更新本文件夹下所有文件的路径
        for image in folder.files.values():
            old_t = old_target / image.meta.fullname
            new_t = new_target / image.meta.fullname
            image.targets.discard(old_t)
            image.targets.add(new_t)
            self.path_to_id.pop(old_t, None)
            self.path_to_id[new_t] = image.meta.id

        # 原地更新文件夹自身的 target（old_target 可能已被调用方提前移除）
        self.path_to_id.pop(old_target, None)
        folder.target = new_target
        self.path_to_id[new_target] = folder.meta.id

        # 递归处理子文件夹
        for child_name, child_folder in folder.subfolders.items():
            self._repath_folder(child_folder, new_target / child_name)

    def rename_node(self, old_path: "Path", new_path: "Path") -> bool:
        """重命名或移动素材。"""
        if old_path not in self.path_to_id or new_path in self.path_to_id:
            return False
        old_parent = old_path.parent
        new_parent = new_path.parent
        if new_parent not in self.path_to_id:
            return False

        file_id = self.path_to_id.pop(old_path)
        self.path_to_id[new_path] = file_id
        _old_parent = self.folder_id_map[self.path_to_id[old_parent]]
        _new_parent = self.folder_id_map[self.path_to_id[new_parent]]
        if file_id in self.file_id_map:
            image = self.file_id_map[file_id]
            image.meta.name = new_path.stem
            image.meta.ext = new_path.suffix.lstrip(".")
            image.save_meta()
            image.targets.remove(old_path)
            image.targets.add(new_path)
            _old_parent.files.pop(old_path.name)
            _new_parent.files[image.meta.fullname] = image
            return True

        if file_id in self.folder_id_map:
            folder = self.folder_id_map[file_id]
            folder.meta.name = new_path.name
            _old_parent.subfolders.pop(old_path.name)
            self._repath_folder(folder, new_path)
            _new_parent.subfolders[folder.meta.fullname] = folder
            self.src.save_meta()
            return True

        return False
