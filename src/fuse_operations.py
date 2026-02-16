import logging
from errno import ENOENT
from pathlib import Path
from stat import S_IFDIR, S_IFREG
from typing import override

from fuse import FuseOSError, LoggingMixIn, Operations

from .source import EagleLibrarySource


class EagleLibrary(Operations, LoggingMixIn):
    """Eagle 素材库 FUSE 文件系统。

    实现 FUSE Operations 接口，将文件系统操作转换为对 Eagle 素材库的操作。
    用户可以通过标准文件系统接口访问素材库中的素材。

    映射关系：
        - FUSE 路径 "/文件夹名/素材名.ext" -> images/{id}/{name}.{ext}
        - FUSE 目录结构 -> Eagle 文件夹层级
        - FUSE 文件 -> Eagle 素材

    Attributes:
        src: Eagle 素材库数据源。
        target: FUSE 挂载目标路径。

    Example:
        ```python
        from pathlib import Path
        from fuse import FUSE

        library = EagleLibrary(Path("test.library"), Path("mount"))
        FUSE(library, "mount", foreground=True)
        ```
    """

    def __init__(self, src_path: Path, target_path: Path) -> None:
        """初始化 FUSE 文件系统。

        Args:
            src_path: Eagle 素材库的源路径。
            target_path: FUSE 挂载的目标路径。
        """
        logging.info(f"Mounting {src_path} to {target_path}")
        self.src = EagleLibrarySource(src_path)
        self.target = target_path

    @override
    def chmod(self, path: str, mode: int) -> None:
        """修改文件权限。

        Args:
            path: 文件路径。
            mode: 新的权限模式。

        Note:
            在 Eagle 素材库中可能无实际意义。
        """
        return super().chmod(path, mode)

    @override
    def chown(self, path: str, uid: int, gid: int) -> None:
        """修改文件所有者。

        Args:
            path: 文件路径。
            uid: 用户 ID。
            gid: 组 ID。

        Note:
            在 Eagle 素材库中可能无实际意义。
        """
        return super().chown(path, uid, gid)

    @override
    def create(self, path: str, mode: int, fi=None) -> int:
        """创建新文件。

        在 Eagle 素材库中创建新素材。

        Args:
            path: 新文件的路径。
            mode: 文件权限模式。
            fi: 文件信息（可选）。

        Returns:
            文件句柄。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        self.src.create_file(path, mode)
        return 0

    @override
    def open(self, path: str, flags: int) -> int:
        """打开文件。

        Args:
            path: 文件路径。
            flags: 打开标志（如 O_RDONLY、O_WRONLY 等）。

        Returns:
            文件句柄。
        """
        file_obj = self.src.get_file(path)
        if file_obj is None:
            raise FuseOSError(ENOENT)
        return 0

    @override
    def destroy(self, path: str) -> None:
        """销毁文件系统。

        在文件系统卸载时调用，用于清理资源。

        Args:
            path: 根路径。
        """
        return super().destroy(path)

    @override
    def getattr(self, path: str, fh=None) -> dict:
        """获取文件或目录属性。

        返回文件/目录的元数据，包括类型、权限、大小等。

        Args:
            path: 文件或目录路径。
            fh: 文件句柄（可选）。

        Returns:
            包含文件属性的字典。

        Raises:
            FuseOSError: 文件/目录不存在时抛出 ENOENT。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        # 检查是否为目录
        folder = self.src.get_folder(path)
        if folder:
            return {
                "st_mode": S_IFDIR | 0o755,
                "st_nlink": 2 + len(folder.children_),
                "st_mtime": folder.modificationTime / 1000,
            }

        # 检查是否为文件
        file_obj = self.src.get_file(path)
        if file_obj:
            return {
                "st_mode": S_IFREG | 0o644,
                "st_nlink": 1,
                "st_size": file_obj.size,
                "st_mtime": file_obj.mtime / 1000,
                "st_ctime": file_obj.btime / 1000,
            }

        # 不存在则抛出异常
        raise FuseOSError(ENOENT)

    @override
    def getxattr(self, path: str, name: str, position: int = 0) -> bytes:
        """获取扩展属性。

        可用于存储 Eagle 特有的元数据，如标签、注释等。

        Args:
            path: 文件路径。
            name: 属性名。
            position: 位置（用于大属性值）。

        Returns:
            属性值。

        Todo:
            实现扩展属性读取，映射到 Eagle 元数据。
        """
        print("getxattr", path, name, position)
        return super().getxattr(path, name, position)

    @override
    def listxattr(self, path: str) -> list[str]:
        """列出所有扩展属性。

        Args:
            path: 文件路径。

        Returns:
            属性名列表。

        Todo:
            实现扩展属性列表，返回可用的元数据字段。
        """
        print("listxattr", path)
        return super().listxattr(path)

    @override
    def readdir(self, path: str, fh: int) -> list[str]:
        """列出目录内容。

        返回指定目录下的所有子文件夹和文件。

        Args:
            path: 目录路径。
            fh: 文件句柄。

        Returns:
            目录项列表，包含 "." 和 ".." 以及所有子项名称。

        Implementation:
            1. 获取文件夹对象。
            2. 添加子文件夹名称。
            3. 添加该文件夹下的素材名称。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        entries = [".", ".."]
        folder = self.src.get_folder(path)
        if folder is None:
            return entries

        # 添加子文件夹
        for child in folder.children_:
            entries.append(child.name)

        # 添加文件
        files = self.src.dir_file_map.get(folder.id, {})
        for file_name in files:
            entries.append(file_name)

        return entries

    @override
    def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
        """读取文件内容。

        从 Eagle 素材库中读取素材文件的实际内容。

        映射流程：
            1. 通过 FUSE 路径获取文件对象。
            2. 根据文件 ID 和扩展名构建实际文件路径。
            3. 读取 images/{id}/{name}.{ext} 文件内容。
            4. 返回指定偏移量和大小的数据。

        Args:
            path: FUSE 文件系统中的文件路径，如 "/文件夹名/素材名.pdf"。
            size: 读取字节数。
            offset: 读取偏移量。
            fh: 文件句柄（当前未使用）。

        Returns:
            读取的数据字节。如果文件不存在则返回空字节。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        # 获取文件对象
        file_obj = self.src.get_file(path)
        if file_obj is None:
            return b""

        # 构建实际文件路径：images/{id}/{name}.{ext}
        file_path = self.src.get_source_path(file_obj)

        # 读取文件内容
        if not file_path.exists():
            return b""

        with Path(file_path).open("rb") as f:
            f.seek(offset)
            return f.read(size)

    @override
    def readlink(self, path: str) -> str:
        """读取符号链接目标。

        Args:
            path: 符号链接路径。

        Returns:
            链接目标路径。

        Note:
            Eagle 素材库不使用符号链接。
        """
        return super().readlink(path)

    @override
    def removexattr(self, path: str, name: str) -> None:
        """删除扩展属性。

        Args:
            path: 文件路径。
            name: 属性名。
        """
        return super().removexattr(path, name)

    @override
    def write(self, path: str, data: bytes, offset: int, fh: int) -> int:
        """写入文件内容。

        将数据写入 Eagle 素材库中的素材文件。
        如果文件不存在，先创建文件再写入。

        Args:
            path: 文件路径。
            data: 要写入的数据。
            offset: 写入偏移量。
            fh: 文件句柄。

        Returns:
            实际写入的字节数。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        file_obj = self.src.get_file(path)
        if file_obj is None:
            # 文件不存在，先创建
            file_obj = self.src.create_file(path, 0o644)
        return self.src.write_file(file_obj.id, data, offset)

    @override
    def mkdir(self, path: str, mode: int) -> None:
        """创建目录。

        在 Eagle 素材库中创建新文件夹。

        Args:
            path: 新目录路径。
            mode: 目录权限模式。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        self.src.create_folder(path)

    @override
    def rmdir(self, path: str) -> None:
        """删除目录。

        删除 Eagle 素材库中的空文件夹。

        Args:
            path: 目录路径。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        self.src.delete_folder(path)

    @override
    def rename(self, old: str, new: str) -> None:
        """重命名或移动文件/目录。

        可以改变素材名称或移动素材到其他文件夹。

        Args:
            old: 原路径。
            new: 新路径。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        # 判断是文件还是目录
        if self.src.get_file(old):
            self.src.rename_file(old, new)
        elif self.src.get_folder(old):
            self.src.rename_folder(old, new)
        else:
            raise FuseOSError(ENOENT)

    @override
    def truncate(self, path: str, length: int, fh=None) -> None:
        """截断文件。

        Args:
            path: 文件路径。
            length: 目标长度。
            fh: 文件句柄（可选）。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        file_obj = self.src.get_file(path)
        if file_obj is None:
            raise FuseOSError(ENOENT)
        self.src.truncate_file(file_obj.id, length)

    @override
    def unlink(self, path: str) -> None:
        """删除文件。

        在 Eagle 素材库中标记素材为已删除。

        Args:
            path: 文件路径。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        self.src.delete_file(path)

    @override
    def utimens(self, path: str, times=None) -> int:
        """修改文件时间戳。

        更新素材的修改时间。

        Args:
            path: 文件路径。
            times: 时间元组 (atime, mtime)。

        Returns:
            0 表示成功。
        """
        # 检查增量更新
        self.src.check_and_update_cache()

        self.src.update_file_time(path, times)
        return 0
