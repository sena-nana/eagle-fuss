from msgspec import Struct, field

from src.type import ID, Stem  # noqa: TC001


class Rule(Struct, frozen=True):
    """智能文件夹筛选规则。

    定义了智能文件夹中单个筛选条件的规则，
    用于根据素材属性进行自动筛选。

    Attributes:
        property: 要筛选的属性名称，如 "tags"、"name"、"ext" 等。
        method: 筛选方法，如 "contains"、"equals"、"startsWith" 等。
        value: 筛选值列表，规则将根据这些值进行匹配。
        hash_key: Eagle 内部使用的哈希键，用于 UI 状态管理。
    """

    property: str
    """要筛选的属性名称，如 "tags"、"name"、"ext" 等。"""

    method: str
    """筛选方法，如 "contains"、"equals"、"startsWith" 等。"""

    value: list[str]
    """筛选值列表，规则将根据这些值进行匹配。"""


class Condition(Struct, frozen=True):
    """智能文件夹筛选条件。

    由多个规则组合而成的筛选条件，支持逻辑组合。

    Attributes:
        rules: 规则列表，包含多个筛选规则。
        match: 规则匹配模式，如 "all"（全部匹配）、"any"（任一匹配）。
        boolean: 布尔逻辑，如 "and"、"or"。
        hash_key: Eagle 内部使用的哈希键。
    """

    rules: list[Rule]
    """规则列表，包含多个筛选规则。"""

    match: str
    """规则匹配模式，如 "all"（全部匹配）、"any"（任一匹配）。"""

    boolean: str
    """布尔逻辑，如 "and"、"or"。"""


class Folder(Struct):
    """Eagle 素材库文件夹模型。

    表示素材库中的文件夹，支持树形层级结构。
    文件夹可以包含子文件夹和素材文件。

    Attributes:
        id: 文件夹唯一标识符。
        name: 文件夹显示名称。
        description: 文件夹描述信息。
        modificationTime: 最后修改时间（毫秒时间戳）。
        tags: 文件夹标签列表。
        children_: 子文件夹列表（从 JSON 的 "children" 字段映射）。
        conditions: 智能文件夹的筛选条件列表。

    Note:
        subdirs 和 subfiles 字段在 __post_init__ 中填充，
        用于建立父子关系的弱引用映射。
    """

    id: ID = ""
    """文件夹唯一标识符。"""

    name: Stem = ""
    """文件夹显示名称。"""

    description: str = ""
    """文件夹描述信息。"""

    modificationTime: int = 0
    """最后修改时间（毫秒时间戳）。"""

    tags: list[str] = field(default_factory=list)
    """文件夹标签列表。"""

    children_: "list[Folder]" = field(name="children", default_factory=list)
    """子文件夹列表（从 JSON 的 "children" 字段映射）。"""

    conditions: list = field(default_factory=list)
    """智能文件夹的筛选条件列表。"""

    # password: str
    # passwordTips: str
    # iconColor: str = ""


class TagGroup(Struct, frozen=True):
    """标签组模型。

    用于将相关标签组织在一起，便于管理和使用。

    Attributes:
        id: 标签组唯一标识符。
        name: 标签组显示名称。
        tags: 包含的标签列表。
    """

    id: ID
    """标签组唯一标识符。"""

    name: str
    """标签组显示名称。"""

    tags: list[str]
    """包含的标签列表。"""


class Meta(Struct):
    """Eagle 素材库主元数据模型。

    对应素材库根目录下的 metadata.json 文件，
    包含素材库的整体结构和配置信息。

    Attributes:
        folders: 顶层文件夹列表。
        smartFolders: 智能文件夹列表。
        quickAccess: 快速访问项列表。
        tagsGroups: 标签组列表。
        modificationTime: 素材库最后修改时间（毫秒时间戳）。
        applicationVersion: Eagle 应用版本号。
        _subdirs: 顶层文件夹的弱引用字典，用于快速查找。

    Example:
        ```python
        meta = json.decode(metadata_json, type=Meta)
        folder = meta.find("设计素材/图标")
        ```
    """

    folders: list[Folder]
    """顶层文件夹列表。"""

    smartFolders: list[Folder]
    """智能文件夹列表。"""

    quickAccess: list
    """快速访问项列表。"""

    tagsGroups: list
    """标签组列表。"""

    modificationTime: int
    """素材库最后修改时间（毫秒时间戳）。"""

    applicationVersion: str
    """Eagle 应用版本号。"""


class Palette(Struct, frozen=True):
    """素材调色板颜色信息。

    表示素材图片中提取的主要颜色及其占比。

    Attributes:
        color: RGB 颜色值列表，如 [250, 248, 249]。
        ratio: 该颜色在图片中的占比百分比。
        hash_key: Eagle 内部使用的哈希键。
    """

    color: list[int]
    """RGB 颜色值列表，如 [250, 248, 249]。"""

    ratio: float
    """该颜色在图片中的占比百分比。"""


class File(Struct):
    """Eagle 素材文件模型。

    表示素材库中的单个素材，对应 images/{id}/metadata.json 文件。
    包含素材的所有元数据信息。

    Attributes:
        id: 素材唯一标识符。
        name: 素材显示名称（不含扩展名）。
        size: 文件大小（字节）。
        btime: 创建时间（毫秒时间戳）。
        mtime: 修改时间（毫秒时间戳）。
        ext: 文件扩展名，如 "png"、"pdf"。
        tags: 标签列表。
        folders: 所属文件夹 ID 列表。
        isDeleted: 是否已标记为删除。
        url: 来源 URL。
        annotation: 用户注释。
        modificationTime: 元数据最后修改时间（毫秒时间戳）。
        height: 图片高度（像素）。
        width: 图片宽度（像素）。
        lastModified: 最后修改时间（毫秒时间戳）。
        palettes: 调色板颜色列表。

    Note:
        实际素材文件存储在 images/{id}/{name}.{ext}。
        缩略图存储在 images/{id}/{name}_thumbnail.png。
    """

    id: ID
    """素材唯一标识符。"""

    name: Stem
    """素材显示名称（不含扩展名）。"""

    size: int
    """文件大小（字节）。"""

    btime: int
    """创建时间（毫秒时间戳）。"""

    mtime: int
    """修改时间（毫秒时间戳）。"""

    ext: str
    """文件扩展名，如 "png"、"pdf"。"""

    tags: list[str]
    """标签列表。"""

    folders: list[str]
    """所属文件夹 ID 列表。"""

    isDeleted: bool
    """是否已标记为删除。"""

    url: str
    """来源 URL。"""

    annotation: str
    """用户注释。"""

    modificationTime: int
    """元数据最后修改时间（毫秒时间戳）。"""

    height: int
    """图片高度（像素）。"""

    width: int
    """图片宽度（像素）。"""

    lastModified: int
    """最后修改时间（毫秒时间戳）。"""

    palettes: list[Palette]
    """调色板颜色列表。"""

    @property
    def fullname(self) -> str:
        return f"{self.name}.{self.ext}"
