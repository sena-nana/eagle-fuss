import logging
from pathlib import Path

from pyfuse3 import close, default_options, init, main
from trio import run

from src import EagleLibrary

logging.basicConfig(level=logging.INFO)


def mount(path: Path):
    logging.info(f"挂载 {path}")
    lib = EagleLibrary(path)
    target = Path.cwd() / path.stem
    target.mkdir(exist_ok=True)
    options = set(default_options)
    options.add("fsname=" + str(hash(path.stem)))
    init(lib, str(target), options)


def mount_all() -> None:
    paths = [dir for dir in Path.cwd().iterdir() if dir.is_dir() and dir.suffix == ".library"]
    for path in paths:
        try:
            mount(path)
        except Exception as e:
            logging.error(e)
            continue
    try:
        run(main)
    except Exception as e:
        close(unmount=True)
        raise e


def test():
    mount(Path.cwd() / "书.library")
    try:
        run(main)
    except Exception as e:
        close(unmount=True)
        raise e


test()
