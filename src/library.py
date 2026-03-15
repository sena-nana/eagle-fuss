import logging
from io import BytesIO
from pathlib import Path
from typing import ClassVar

from msgspec import Struct, field, json
from PIL import Image

from .core import IMAGE_EXTENSIONS, new_id, now
from .models import File, Folder, Meta
from .type import ID

mtime_loader = json.Decoder(dict[ID, int]).decode
saver = json.Encoder().encode
ROOT_ID = "root"
VOID_ID = "null"


class Source(Struct, frozen=True):
    path: "Path"
    meta: Meta
    _meta_loader = json.Decoder(Meta).decode

    @classmethod
    def load(cls, path: "Path"):
        return Source(
            path=path, meta=cls._meta_loader((path / "metadata.json").read_text(encoding="utf-8"))
        )

    def image(self, id: "ID"):
        return ImageSource.load(self.path, id)

    def read_mtime(self):
        return mtime_loader((self.path / "mtime.json").read_text(encoding="utf-8"))

    def read_meta(self):
        return self._meta_loader((self.path / "metadata.json").read_text(encoding="utf-8"))

    def save_meta(self):
        # TODO: 从source重新解析目录树
        (self.path / "metadata.json").write_bytes(saver(self.meta))

    def create_root(self):
        _void_folder = Folder(id=VOID_ID, name="未分类")
        root_folder = FolderSource(
            Folder(id=ROOT_ID, name="根目录", children=[_void_folder, *self.meta.folders]),
            Path(),
            self.path,
        )
        void_folder = root_folder / _void_folder
        return root_folder, void_folder


class ImageSource(Struct):
    meta: File
    source: "Path"
    targets: set["Path"] = field(default_factory=set)
    _loader: ClassVar = json.Decoder(File).decode

    @classmethod
    def load(cls, src: "Path", id: "ID"):
        folder = src / "images" / (id + ".info")
        return ImageSource(
            meta=cls._loader((folder / "metadata.json").read_text(encoding="utf-8")), source=folder
        )

    @property
    def _data(self):
        return self.source / self.meta.fullname

    @property
    def _thumb(self):
        return self.source / f"{self.meta.id}_thumbnail.png"

    @property
    def is_image(self):
        return self.meta.ext.lower() in IMAGE_EXTENSIONS

    def read_data(self):
        return self._data.read_bytes()

    def read_thumb(self):
        return self._thumb.read_bytes() if self._thumb.exists() else b""

    def add_mtime(self):
        f = self.source.parent.parent / "mtime.json"
        mtime = mtime_loader(f.read_text(encoding="utf-8"))
        mtime[self.meta.id] = self.meta.modificationTime
        f.write_bytes(saver(mtime))

    def save_meta(self):
        if not self.source.exists():
            self.source.mkdir(parents=True)
        self.meta.modificationTime = now()
        self.add_mtime()
        (self.source / "metadata.json").write_bytes(saver(self.meta))

    def save_data(self, data: bytes):
        if not self.source.exists():
            self.source.mkdir(parents=True)
        (self.source / self.meta.fullname).write_bytes(data)
        self.meta.size = len(data)
        self.save_meta()
        if self.meta.ext.lower() not in IMAGE_EXTENSIONS:
            return True
        try:
            with Image.open(BytesIO(data)) as img:
                ori_size = img.size
                scale = min(1, 320 / min(ori_size))
                img.convert("RGB").resize(
                    (int(ori_size[0] * scale), int(ori_size[1] * scale)), Image.Resampling.LANCZOS
                ).save(self._thumb, "PNG", optimize=True)
            return True
        except Exception as e:
            logging.error(f"生成缩略图失败 {self.source}: {e}")
            return False

    def delete(self):
        self.meta.isDeleted = True
        self.save_meta()


class FolderSource(Struct):
    meta: Folder
    target: "Path"
    library_path: "Path"
    files: dict[str, "ImageSource"] = field(default_factory=dict)
    subfolders: dict[str, "FolderSource"] = field(default_factory=dict)

    def add_file(self, file: ImageSource):
        if file.meta.fullname in self.files or file.meta.fullname in self.subfolders:
            return False
        self.files[file.meta.fullname] = file
        return True

    def __truediv__(self, other: Folder):
        return FolderSource(other, self.target / other.fullname, self.library_path)

    def new_file(self, data: bytes, stem: str, ext: str):
        filename = f"{stem}.{ext}"
        if filename in self.files or filename in self.subfolders:
            return None
        size = Image.open(BytesIO(data)).size if ext in IMAGE_EXTENSIONS else (0, 0)
        time = now()
        file = File(
            id=new_id(),
            name=stem,
            size=len(data),
            btime=time,
            mtime=time,
            ext=ext,
            tags=[],
            folders=[self.meta.id],
            isDeleted=False,
            url="",
            annotation="",
            modificationTime=time,
            height=size[1],
            width=size[0],
            lastModified=time,
            palettes=[],
        )
        info_path = self.library_path / "images" / (file.id + ".info")
        image = ImageSource(file, info_path, targets={self.target / file.fullname})
        self.files[image.meta.fullname] = image
        image.save_data(data)
        return image

    def new_subfolder(self, name: str):
        if name in self.subfolders or name in self.files:
            return None
        time = now()
        folder = Folder(
            id=new_id(),
            name=name,
            modificationTime=time,
        )
        subfolder = self / folder
        self.subfolders[folder.fullname] = subfolder
        self.meta.modificationTime = time
        self.meta.children.append(folder)
        return subfolder
