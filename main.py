import logging
from pathlib import Path

from fuse import FUSE

from src.fs import EagleLibrary


def main() -> None:
    """主入口函数。

    自动查找当前目录下所有 .library 目录，
    并将它们挂载为同名目录（去除 .library 后缀）。

    Example:
        当前目录有 test.library/，则挂载到 test/。
    """
    paths = [dir for dir in Path.cwd().iterdir() if dir.is_dir() and dir.suffix == ".library"]
    for path in paths:
        logging.info(f"Mounting {path}")
        target = Path.cwd() / path.stem.removesuffix(".library")
        _ = FUSE(EagleLibrary(path, target), target, foreground=True, nothreads=True)
    input("Press Enter to exit...")


main()
