import logging
from pathlib import Path

from pyfuse3 import close, default_options, init, main
from trio import run

from src import EagleLibrary

logging.basicConfig(level=logging.INFO)


def test() -> None:
    paths = [dir for dir in Path.cwd().iterdir() if dir.is_dir() and dir.suffix == ".library"]
    for path in paths:
        try:
            logging.info(f"挂载 {path}")
            lib = EagleLibrary(path)
            target = Path.cwd() / path.stem
            target.mkdir(exist_ok=True)
            options = set(default_options)
            options.add("fsname=" + path.stem)
            init(lib, str(target), options)
            break
        except Exception as e:
            logging.error(e)
            continue
    try:
        run(main)
    except Exception as e:
        close(unmount=True)
        raise e


test()
