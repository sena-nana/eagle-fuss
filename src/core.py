import time


def now() -> int:
    """获取当前时间的毫秒时间戳。

    Returns:
        当前时间的毫秒级 Unix 时间戳。
    """
    return int(time.time() * 1000)


# 支持的图片格式（用于生成缩略图）
IMAGE_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "gif",
    "bmp",
    "webp",
    "tiff",
    "tif",
    "ico",
    "svg",
    "heic",
    "heif",
    "avif",
    "jfif",
    "pjpeg",
    "pjp",
}
