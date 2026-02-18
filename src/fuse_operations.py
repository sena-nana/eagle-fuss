import errno
import logging
import os
import stat
from typing import TYPE_CHECKING, override

from pyfuse3 import (
    ROOT_INODE,
    EntryAttributes,
    FileHandleT,
    FileInfo,
    FileNameT,
    FlagT,
    FUSEError,
    InodeT,
    ModeT,
    Operations,
    ReaddirToken,
    RequestContext,
    SetattrFields,
    StatvfsData,
    readdir_reply,
)

from .models import File, Folder
from .source import NULL_INODE, EagleLibrarySource

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class _SimpleRequestContext:
    """简单的RequestContext模拟，用于内部调用。"""

    def __init__(self) -> None:
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.pid = os.getpid()
        self.umask = 0o022

    @override
    def __getstate__(self) -> None:
        return None


class EagleLibrary(Operations):
    """Eagle 素材库 FUSE 文件系统。

    实现 FUSE Operations 接口，将文件系统操作转换为对 Eagle 素材库的操作。
    用户可以通过标准文件系统接口访问素材库中的素材。
    """

    def __init__(self, src_path: "Path") -> None:
        """初始化 FUSE 文件系统。

        Args:
            src_path: Eagle 素材库的根目录路径
        """
        super().__init__()
        logging.info(f"Mounting {src_path}")
        self.src = EagleLibrarySource(src_path)
        self._open_files: dict[FileHandleT, tuple[InodeT, int]] = {}
        """文件句柄映射：fh -> (inode, type)，type: 0=目录，1=文件"""
        self._next_fh = 1
        """下一个可用的文件句柄ID"""
        self._ctx = _SimpleRequestContext()
        """内部使用的请求上下文"""

    # ==================== 基本操作 ====================
    @override
    async def lookup(
        self, parent_inode: InodeT, name: FileNameT, ctx: "RequestContext"
    ) -> "EntryAttributes":
        """查找目录项并返回其属性。

        Args:
            parent_inode: 父目录的inode
            name: 文件名（bytes）
            ctx: 请求上下文

        Returns:
            EntryAttributes: 文件/目录的属性

        Raises:
            FUSEError: 如果文件不存在，返回ENOENT
        """
        logging.info(f"lookup(parent_inode={parent_inode}, name={name!r})")
        self.src.update_cache()
        try:
            name_str = name.decode("utf-8", errors="replace")
        except UnicodeDecodeError as e:
            logging.error(f"lookup: Unicode decode error for name {name!r}: {e}")
            raise FUSEError(errno.EINVAL) from e

        # 特殊目录项处理
        if name_str == ".":
            return await self.getattr(parent_inode, ctx)
        if name_str == "..":
            parent_parent = self._get_parent_inode(parent_inode)
            return await self.getattr(parent_parent, ctx)

        children = self.src.children_map.get(parent_inode)
        if children is None:
            # 返回st_ino为0表示负缓存
            attr = EntryAttributes()
            attr.st_ino = InodeT(0)
            attr.generation = 0
            attr.entry_timeout = 300.0  # 5分钟负缓存
            attr.attr_timeout = 300.0
            return attr

        child_inode = children.get(name_str)
        if child_inode is None:
            # 返回st_ino为0表示负缓存
            attr = EntryAttributes()
            attr.st_ino = InodeT(0)
            attr.generation = 0
            attr.entry_timeout = 300.0
            attr.attr_timeout = 300.0
            return attr

        return await self.getattr(child_inode, ctx)

    @override
    async def getattr(self, inode: InodeT, ctx: "RequestContext") -> "EntryAttributes":
        """获取inode属性。

        Args:
            inode: 要获取属性的inode
            ctx: 请求上下文

        Returns:
            EntryAttributes: 属性对象
        """
        logging.info(f"getattr(inode={inode})")
        self.src.update_cache()

        if inode == ROOT_INODE:
            # 根目录
            attr = EntryAttributes()
            attr.st_ino = ROOT_INODE
            attr.generation = 0
            attr.entry_timeout = 300.0
            attr.attr_timeout = 300.0
            attr.st_mode = stat.S_IFDIR | 0o755
            attr.st_nlink = 2  # 自身和父目录
            attr.st_size = 4096
            attr.st_uid = os.getuid()
            attr.st_gid = os.getgid()
            attr.st_atime_ns = self.src.meta.modificationTime * 1_000_000
            attr.st_mtime_ns = self.src.meta.modificationTime * 1_000_000
            attr.st_ctime_ns = self.src.meta.modificationTime * 1_000_000
            attr.st_birthtime_ns = self.src.meta.modificationTime * 1_000_000
            attr.st_blksize = 4096
            attr.st_blocks = 1
            return attr

        obj = self.src.inode_map.get(inode)
        if obj is None:
            raise FUSEError(errno.ENOENT)

        attr = EntryAttributes()
        attr.st_ino = inode
        attr.generation = 0
        attr.entry_timeout = 300.0
        attr.attr_timeout = 300.0

        if isinstance(obj, Folder):
            # 文件夹
            attr.st_mode = stat.S_IFDIR | 0o755
            # nlink = 2 (自身和.) + 子目录数
            subdir_count = 0
            children = self.src.children_map.get(inode, {})
            for child_inode in children.values():
                child_obj = self.src.inode_map.get(child_inode)
                if child_obj and isinstance(child_obj, Folder):
                    subdir_count += 1
            attr.st_nlink = 2 + subdir_count
            attr.st_size = 4096
            attr.st_uid = os.getuid()
            attr.st_gid = os.getgid()
            attr.st_atime_ns = obj.modificationTime * 1_000_000
            attr.st_mtime_ns = obj.modificationTime * 1_000_000
            attr.st_ctime_ns = obj.modificationTime * 1_000_000
            attr.st_birthtime_ns = obj.modificationTime * 1_000_000
            attr.st_blksize = 4096
            attr.st_blocks = 1
        else:
            # 文件
            attr.st_mode = stat.S_IFREG | 0o644
            attr.st_nlink = 1
            attr.st_size = obj.size
            attr.st_uid = os.getuid()
            attr.st_gid = os.getgid()
            attr.st_atime_ns = obj.mtime * 1_000_000
            attr.st_mtime_ns = obj.mtime * 1_000_000
            attr.st_ctime_ns = obj.modificationTime * 1_000_000
            attr.st_birthtime_ns = obj.btime * 1_000_000
            attr.st_blksize = 4096
            attr.st_blocks = (obj.size + 511) // 512

        return attr

    @override
    async def opendir(self, inode: InodeT, ctx: "RequestContext") -> FileHandleT:
        """打开目录。

        Args:
            inode: 目录inode
            ctx: 请求上下文

        Returns:
            FileHandleT: 目录句柄
        """
        logging.info(f"opendir(inode={inode})")
        self.src.update_cache()
        if inode != ROOT_INODE and inode not in self.src.inode_map:
            logging.warning(f"opendir: inode {inode} not found")
            raise FUSEError(errno.ENOENT)

        fh = FileHandleT(self._next_fh)
        self._next_fh += 1
        self._open_files[fh] = (inode, 0)  # 0表示目录
        logging.debug(f"opendir: created file handle {fh} for inode {inode}")
        return fh

    @override
    async def readdir(self, fh: FileHandleT, start_id: int, token: "ReaddirToken") -> None:
        """读取目录内容。

        Args:
            fh: 目录句柄
            start_id: 起始条目ID
            token: readdir token
        """
        logging.info(f"readdir(fh={fh}, start_id={start_id})")
        self.src.update_cache()
        if fh not in self._open_files:
            logging.error(f"readdir: invalid file handle {fh}")
            raise FUSEError(errno.EBADF)

        inode, file_type = self._open_files[fh]
        if file_type != 0:
            logging.error(f"readdir: file handle {fh} is not a directory (type={file_type})")
            raise FUSEError(errno.EBADF)

        children = self.src.children_map.get(inode, {})
        logging.debug(f"readdir: directory inode {inode} has {len(children)} children")

        # 添加"."和".."
        entries = [(".", inode), ("..", self._get_parent_inode(inode))]

        # 添加子项
        for name, child_inode in children.items():
            entries.append((name, child_inode))

        # 排序确保稳定顺序
        entries.sort(key=lambda x: x[0])
        logging.debug(f"readdir: total entries {len(entries)}, starting from {start_id}")

        # 从start_id开始发送
        sent_count = 0
        for i in range(start_id, len(entries)):
            name, child_inode = entries[i]
            # 跳过"."和".."的inode增加
            if name in (".", ".."):
                attr = await self.getattr(child_inode, self._ctx)  # type: ignore
                if not readdir_reply(token, name.encode("utf-8"), attr, i + 1):
                    logging.debug(f"readdir: stopped at entry {i} (special)")
                    break
                sent_count += 1
                continue

            obj = self.src.inode_map.get(child_inode)
            if obj is None:
                logging.warning(f"readdir: child inode {child_inode} not found in inode_map")
                continue

            attr = await self.getattr(child_inode, self._ctx)  # type: ignore
            if not readdir_reply(token, name.encode("utf-8"), attr, i + 1):
                logging.debug(f"readdir: stopped at entry {i} (name={name})")
                break
            sent_count += 1

        logging.info(f"readdir: sent {sent_count} entries")

    @override
    async def releasedir(self, fh: FileHandleT) -> None:
        """释放目录句柄。

        Args:
            fh: 目录句柄
        """
        if fh in self._open_files:
            del self._open_files[fh]

    # ==================== 文件操作 ====================
    @override
    async def open(self, inode: InodeT, flags: FlagT, ctx: "RequestContext") -> "FileInfo":
        """打开文件。

        Args:
            inode: 文件inode
            flags: 打开标志
            ctx: 请求上下文

        Returns:
            FileInfo: 文件信息
        """
        logging.info(f"open(inode={inode}, flags={flags})")
        self.src.update_cache()
        obj = self.src.inode_map.get(inode)
        if obj is None:
            logging.error(f"open: inode {inode} not found")
            raise FUSEError(errno.ENOENT)

        if isinstance(obj, Folder):
            logging.error(f"open: inode {inode} is a directory, not a file")
            raise FUSEError(errno.EISDIR)

        fh = FileHandleT(self._next_fh)
        self._next_fh += 1
        self._open_files[fh] = (inode, 1)  # 1表示文件
        logging.debug(f"open: created file handle {fh} for inode {inode}")
        return FileInfo(fh=fh, direct_io=False, keep_cache=True, nonseekable=False)

    @override
    async def read(self, fh: FileHandleT, off: int, size: int) -> bytes:
        """读取文件数据。

        Args:
            fh: 文件句柄
            off: 偏移量
            size: 读取大小

        Returns:
            bytes: 读取的数据
        """
        logging.info(f"read(fh={fh}, off={off}, size={size})")
        if fh not in self._open_files:
            raise FUSEError(errno.EBADF)

        inode, file_type = self._open_files[fh]
        if file_type != 1:
            raise FUSEError(errno.EBADF)

        obj = self.src.inode_map.get(inode)
        if obj is None:
            raise FUSEError(errno.EBADF)

        if isinstance(obj, Folder):
            raise FUSEError(errno.EISDIR)

        # 读取文件数据
        data = self.src.read_image(obj)
        if off >= len(data):
            return b""

        return data[off : off + size]

    @override
    async def write(self, fh: FileHandleT, off: int, buf: bytes) -> int:
        """写入文件数据。

        Args:
            fh: 文件句柄
            off: 偏移量
            buf: 要写入的数据

        Returns:
            int: 写入的字节数
        """
        logging.info(f"write(fh={fh}, off={off}, size={len(buf)})")
        if fh not in self._open_files:
            raise FUSEError(errno.EBADF)

        inode, file_type = self._open_files[fh]
        if file_type != 1:
            raise FUSEError(errno.EBADF)

        obj = self.src.inode_map.get(inode)
        if obj is None:
            raise FUSEError(errno.EBADF)

        if isinstance(obj, Folder):
            raise FUSEError(errno.EISDIR)

        # 目前Eagle库不支持部分写入，需要读取整个文件然后修改
        # 简化实现：替换整个文件
        self.src.write_file(inode, buf)
        return len(buf)

    @override
    async def create(
        self,
        parent_inode: InodeT,
        name: FileNameT,
        mode: ModeT,
        flags: FlagT,
        ctx: "RequestContext",
    ) -> tuple["FileInfo", "EntryAttributes"]:
        """创建并打开文件。

        Args:
            parent_inode: 父目录inode
            name: 文件名
            mode: 文件模式
            flags: 打开标志
            ctx: 请求上下文

        Returns:
            Tuple[FileInfo, EntryAttributes]: 文件句柄和属性
        """
        logging.info(f"create(parent_inode={parent_inode}, name={name!r})")
        self.src.update_cache()
        try:
            name_str = name.decode("utf-8", errors="replace")
        except UnicodeDecodeError as e:
            raise FUSEError(errno.EINVAL) from e

        # 检查是否已存在
        children = self.src.children_map.get(parent_inode, {})
        if name_str in children:
            raise FUSEError(errno.EEXIST)

        # 解析文件名和扩展名
        if "." in name_str:
            base_name, ext = name_str.rsplit(".", 1)
        else:
            base_name, ext = name_str, ""

        # 创建空文件
        success = self.src.new_file(parent_inode, base_name, b"", ext)
        if not success:
            raise FUSEError(errno.EIO)

        # 获取新文件的inode
        children = self.src.children_map.get(parent_inode, {})
        new_inode = children.get(name_str)
        if new_inode is None:
            raise FUSEError(errno.EIO)

        # 打开文件
        fh = FileHandleT(self._next_fh)
        self._next_fh += 1
        self._open_files[fh] = (new_inode, 1)

        attr = await self.getattr(new_inode, ctx)
        return FileInfo(fh=fh, direct_io=False, keep_cache=True, nonseekable=False), attr

    @override
    async def release(self, fh: FileHandleT) -> None:
        """释放文件句柄。

        Args:
            fh: 文件句柄
        """
        logging.info(f"release(fh={fh})")
        if fh in self._open_files:
            del self._open_files[fh]

    # ==================== 目录操作 ====================
    @override
    async def mkdir(
        self, parent_inode: InodeT, name: FileNameT, mode: ModeT, ctx: "RequestContext"
    ) -> "EntryAttributes":
        """创建目录。

        Args:
            parent_inode: 父目录inode
            name: 目录名
            mode: 目录模式
            ctx: 请求上下文

        Returns:
            EntryAttributes: 新目录的属性
        """
        logging.info(f"mkdir(parent_inode={parent_inode}, name={name!r})")
        self.src.update_cache()
        try:
            name_str = name.decode("utf-8", errors="replace")
        except UnicodeDecodeError as e:
            raise FUSEError(errno.EINVAL) from e

        success = self.src.new_folder(parent_inode, name_str)
        if not success:
            raise FUSEError(errno.EEXIST)

        # 获取新目录的inode
        children = self.src.children_map.get(parent_inode, {})
        new_inode = children.get(name_str)
        if new_inode is None:
            raise FUSEError(errno.EIO)

        return await self.getattr(new_inode, ctx)

    @override
    async def rmdir(self, parent_inode: InodeT, name: FileNameT, ctx: "RequestContext") -> None:
        """删除目录。

        Args:
            parent_inode: 父目录inode
            name: 目录名
            ctx: 请求上下文
        """
        logging.info(f"rmdir(parent_inode={parent_inode}, name={name!r})")
        self.src.update_cache()
        try:
            name_str = name.decode("utf-8", errors="replace")
        except UnicodeDecodeError as e:
            raise FUSEError(errno.EINVAL) from e

        children = self.src.children_map.get(parent_inode, {})
        child_inode = children.get(name_str)
        if child_inode is None:
            raise FUSEError(errno.ENOENT)

        # 检查是否为空目录
        child_children = self.src.children_map.get(child_inode, {})
        if child_children:
            raise FUSEError(errno.ENOTEMPTY)

        self.src.delete_node(child_inode)

    @override
    async def unlink(self, parent_inode: InodeT, name: FileNameT, ctx: "RequestContext") -> None:
        """删除文件。

        Args:
            parent_inode: 父目录inode
            name: 文件名
            ctx: 请求上下文
        """
        logging.info(f"unlink(parent_inode={parent_inode}, name={name!r})")
        self.src.update_cache()
        try:
            name_str = name.decode("utf-8", errors="replace")
        except UnicodeDecodeError as e:
            raise FUSEError(errno.EINVAL) from e

        children = self.src.children_map.get(parent_inode, {})
        child_inode = children.get(name_str)
        if child_inode is None:
            raise FUSEError(errno.ENOENT)

        obj = self.src.inode_map.get(child_inode)
        if obj is None:
            raise FUSEError(errno.ENOENT)

        if isinstance(obj, Folder):
            raise FUSEError(errno.EISDIR)

        self.src.delete_node(child_inode)

    @override
    async def rename(
        self,
        parent_inode_old: InodeT,
        name_old: FileNameT,
        parent_inode_new: InodeT,
        name_new: FileNameT,
        flags: FlagT,
        ctx: "RequestContext",
    ) -> None:
        """重命名或移动文件/目录。

        Args:
            parent_inode_old: 原父目录inode
            name_old: 原名称
            parent_inode_new: 新父目录inode
            name_new: 新名称
            flags: 重命名标志
            ctx: 请求上下文
        """
        logging.info(
            f"rename(parent_inode_old={parent_inode_old}, name_old={name_old!r}, "  # noqa: ISC002  # pyright: ignore[reportImplicitStringConcatenation]
            f"parent_inode_new={parent_inode_new}, name_new={name_new!r}, flags={flags})"
        )
        self.src.update_cache()
        try:
            old_name_str = name_old.decode("utf-8", errors="replace")
            new_name_str = name_new.decode("utf-8", errors="replace")
        except UnicodeDecodeError as e:
            raise FUSEError(errno.EINVAL) from e

        # 检查源是否存在
        old_children = self.src.children_map.get(parent_inode_old, {})
        if old_name_str not in old_children:
            raise FUSEError(errno.ENOENT)

        # 检查目标是否已存在（根据flags处理）
        new_children = self.src.children_map.get(parent_inode_new, {})
        if new_name_str in new_children:
            # 检查RENAME_NOREPLACE标志
            if flags & 1:  # RENAME_NOREPLACE
                raise FUSEError(errno.EEXIST)
            if flags & 2:  # RENAME_EXCHANGE
                # 交换操作 - 暂不支持
                raise FUSEError(errno.ENOSYS)
            # 覆盖：先删除目标
            target_inode = new_children[new_name_str]
            self.src.delete_node(target_inode)

        # 执行重命名/移动
        self.src.rename_node(old_name_str, new_name_str, parent_inode_new, parent_inode_old)


    # ==================== 辅助方法 ====================

    def _get_parent_inode(self, inode: InodeT) -> InodeT:
        """获取父目录的inode。

        Args:
            inode: 当前inode

        Returns:
            InodeT: 父目录inode
        """
        if inode == ROOT_INODE:
            return ROOT_INODE
        if inode == NULL_INODE:
            return ROOT_INODE

        # 查找文件夹的父目录
        parent = self.src.folder_parent_map.get(inode)
        if parent is not None:
            return parent

        # 对于文件，查找其所在的第一个文件夹
        obj = self.src.inode_map.get(inode)
        if obj is not None and isinstance(obj, File) and obj.folders:
            folder_id = obj.folders[0]
            return self.src.id_map.get(folder_id, ROOT_INODE)

        return ROOT_INODE
