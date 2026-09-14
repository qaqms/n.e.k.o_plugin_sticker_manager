"""纯函数层：零 SDK 依赖、零 IO。

- `catalog`：表情包条目的数据形状、图片格式嗅探、检索与目录文案（给模型看的）。
- `configuration`：`[sticker_manager]` 配置族的 dataclass 视图与默认值。
"""

from .catalog import (
    Sticker,
    detect_image_format,
    format_catalog_for_model,
    new_sticker_id,
    normalize_tags,
    parse_tags_field,
    search_stickers,
    validate_desc,
)
from .configuration import SendSettings, StickerManagerSettings, StorageSettings

__all__ = [
    "SendSettings",
    "Sticker",
    "StorageSettings",
    "StickerManagerSettings",
    "detect_image_format",
    "format_catalog_for_model",
    "new_sticker_id",
    "normalize_tags",
    "parse_tags_field",
    "search_stickers",
    "validate_desc",
]
