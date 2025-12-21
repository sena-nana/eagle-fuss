from weakref import WeakValueDictionary

from msgspec import Struct, field

from src.type import ID, Stem  # noqa: TC001


class Rule(Struct, frozen=True):
    property: str
    method: str
    value: list[str]
    hash_key: str = field(name="$$hashKey")


class Condition(Struct, frozen=True):
    rules: list[Rule]
    match: str
    boolean: str
    hash_key: str = field(name="$$hashKey")


class Folder(Struct, frozen=True):
    id: ID = ""
    name: Stem = ""
    description: str = ""
    modificationTime: int = 0
    tags: list[str] = field(default_factory=list)
    children_: "list[Folder]" = field(name="children", default_factory=list)
    conditions: list = field(default_factory=list)
    subdirs: "WeakValueDictionary[str,Folder|File]" = field(default_factory=WeakValueDictionary)

    # password: str
    # passwordTips: str
    # iconColor: str = ""
    def __post_init__(self):
        for child_dir in self.children_:
            self.subdirs[child_dir.name] = child_dir

    def find(self, path: list[Stem]) -> "Folder|File|None":
        subdir, *rest = path
        for child, v in self.subdirs.items():
            if child == subdir:
                return v.find(rest) if isinstance(v, Folder) and v.subdirs else v
        return None


class TagGroup(Struct, frozen=True):
    id: ID
    name: str
    tags: list[str]


class Meta(Struct, frozen=True):
    folders: list[Folder]
    smartFolders: list[Folder]
    quickAccess: list
    tagsGroups: list
    modificationTime: int
    applicationVersion: str
    _subdirs: "WeakValueDictionary[Stem,Folder]" = field(default_factory=WeakValueDictionary)

    def __post_init__(self):
        for folder in self.folders:
            self._subdirs[folder.name] = folder

    def find(self, path: str) -> "Folder|File|None":
        first, *rest = path.removesuffix("/").split("/")
        if first not in self._subdirs:
            return None
        if not rest:
            return self._subdirs[first]
        return self._subdirs[first].find(rest)


class Palette(Struct, frozen=True):
    color: list[int]
    ratio: int
    hash_key: str = field(name="$$hashKey")


class File(Struct, frozen=True):
    id: ID
    name: Stem
    size: int
    btime: int
    mtime: int
    ext: str
    tags: list[str]
    folders: list[str]
    isDeleted: bool
    url: str
    annotation: str
    modificationTime: int
    height: int
    width: int
    lastModified: int
    palettes: list[Palette]
