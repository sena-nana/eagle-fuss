from msgspec import Struct, convert, field


class Test(Struct):
    _x: int = field(name="x")
    y: list[int] = field(default_factory=list)

    def __post_init__(self):
        self.y.append(self._x)


a = convert({"x": 1}, type=Test)
print(a)
