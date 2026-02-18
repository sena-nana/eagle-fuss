import time


def now() -> int:
    """获取当前时间的毫秒时间戳。

    Returns:
        当前时间的毫秒级 Unix 时间戳。
    """
    return int(time.time() * 1000)


def new_id():
    timestamp = int(time.time() * 1000) - 1090000000000
    worker_id = 2
    process_id = 3
    sequence = 7777777
    snowflake_id = timestamp << 22 | worker_id << 17 | process_id << 12 | sequence
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    base36_id = ""
    num = snowflake_id
    while num > 0:
        num, i = divmod(num, 36)
        base36_id = alphabet[i] + base36_id

    return "M" + base36_id


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
