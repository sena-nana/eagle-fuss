import logging
import time
from pathlib import Path
from stat import S_IFDIR
from typing import TYPE_CHECKING, override

from fuse import FUSE, LoggingMixIn, Operations
from msgspec import json

from .src.models import File, Folder, Meta

if TYPE_CHECKING:
    from src.type import ID


def now():
    return int(time.time() * 1000)


class _EagleLibrarySource:
    def __init__(self, path: Path) -> None:
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

        self._init_cache()

    def _init_cache(self):
        # 建立文件夹和ID的映射
        self.void_folder = Folder(id="null", name="未分类")
        self.meta = json.decode((self.src / "metadata.json").read_text(encoding="utf-8"), type=Meta)
        self.root = Folder(children_=[*self.meta.folders, self.void_folder])
        self.path_dir_map[Path("/")] = self.root

        def _loop_folders(parent: Folder, path: Path):
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

    def _update_cache(self):
        mtime = json.decode(
            (self.src / "mtime.json").read_text(encoding="utf-8"), type=dict[str, int]
        )
        for k, v in mtime.items():
            if v > self.update_time:
                new_file = self._load_file(k)
                if new_file.isDeleted:
                    self.dir_file_map.get(new_file.folders[0], {}).pop(self.id_map[k].name)
                    del self.id_map[k]
                    continue
                if new_file.folders != self.id_map[k].folders:
                    # 文件夹变更，更新映射
                    old_folders = self.id_map[k].folders
                    for old_folder in old_folders:
                        self.dir_file_map.get(old_folder, {}).pop(self.id_map[k].name)
                    for new_folder in new_file.folders:
                        self.dir_file_map.setdefault(new_folder, {})[new_file.name] = new_file
                self.id_map[k] = new_file
        self.update_time = now()

    def get_file(self, path: str) -> File | None:
        path_ = Path(path)
        parent_path = path_.parent
        folder = self.path_dir_map.get(parent_path)
        if not folder:
            return None
        return self.dir_file_map.get(folder.id, {}).get(path_.name)

    def get_folder(self, path: str) -> Folder | None:
        return self.path_dir_map.get(Path(path))

    def _load_file(self, file_id: "ID") -> File:
        return json.decode(
            (self.src / "images" / file_id / "metadata.json").read_text(encoding="utf-8"), type=File
        )


class EagleLibrary(Operations, LoggingMixIn):
    def __init__(self, src_path: Path, target_path: Path) -> None:
        self.src = _EagleLibrarySource(src_path)
        self.target = target_path

    @override
    def chmod(self, path, mode):
        return super().chmod(path, mode)

    @override
    def chown(self, path, uid, gid):
        return super().chown(path, uid, gid)

    @override
    def create(self, path, mode, fi=None):
        """创建文件"""
        print(path, mode, fi)
        return super().create(path, mode, fi)

    @override
    def open(self, path, flags):
        """打开文件"""
        return super().open(path, flags)

    @override
    def destroy(self, path):
        return super().destroy(path)

    @override
    def getattr(self, path, fh=None):
        print("getattr", path, fh)
        return {
            "st_mode": (S_IFDIR | 0o755),
            "st_nlink": 2,
        }

    @override
    def getxattr(self, path, name, position=0):
        print("getxattr", path, name, position)
        return super().getxattr(path, name, position)

    @override
    def listxattr(self, path):
        print("listxattr", path)
        return super().listxattr(path)

    @override
    def readdir(self, path, fh):
        print("readdir", path, fh)
        if path == "/":
            return [".", "..", "123.txt"]
        return [".", ".."]

    @override
    def read(self, path, size, offset, fh):
        print(path, size, offset, fh)
        return super().read(path, size, offset, fh)

    @override
    def readlink(self, path):
        return super().readlink(path)

    @override
    def removexattr(self, path, name):
        return super().removexattr(path, name)

    @override
    def write(self, path, data, offset, fh):
        print(path, data, offset, fh)
        return super().write(path, data, offset, fh)

    @override
    def mkdir(self, path, mode):
        return super().mkdir(path, mode)

    @override
    def rmdir(self, path):
        return super().rmdir(path)

    @override
    def rename(self, old, new):
        return super().rename(old, new)

    @override
    def truncate(self, path, length, fh=None):
        return super().truncate(path, length, fh)

    @override
    def unlink(self, path):
        return super().unlink(path)

    @override
    def utimens(self, path, times=None):
        return super().utimens(path, times)


def main():
    paths = [dir for dir in Path.cwd().iterdir() if dir.is_dir() and dir.stem.endswith(".library")]
    for path in paths:
        logging.info(f"Mounting {path}")
        target = Path.cwd() / path.stem.removesuffix(".library")
        _ = FUSE(EagleLibrary(path, target), target, foreground=True, nothreads=True)


def test():
    _ = FUSE(
        EagleLibrary(Path.cwd() / "test.library", Path.cwd() / "test"),
        str(Path.cwd() / "test"),
        foreground=True,
        nothreads=True,
    )


test()
