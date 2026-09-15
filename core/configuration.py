"""`[sticker_manager]` 配置族的 dataclass 视图（默认值的第三来源）。

三处同源纪律（沿用 our_life 的教训）：本文件的 dataclass 默认值、
`plugin.toml` 的业务段、`config.example.toml` 必须逐键逐值一致，
由 `tests/test_config_docs_sync.py` 钉死。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 上传单张的硬上限：8 MiB。宿主 images.upload 的解码上限是 32 MiB、
# 归一后上限是 8 MiB，表情包比它更严是合理的（表情包不该是相册）。
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


@dataclass(frozen=True)
class SendSettings:
    """发送链路的行为参数。"""

    # 同一角色卡两次发送之间的最小间隔（秒）。防刷屏，也防模型连着甩图。
    cooldown_sec: float = 20.0
    # 静态图走内联（image data part）的上限。payload 整条限 512 KiB、
    # base64 膨胀 4/3，这里再留余量给可能的文字 part 与协议封装。
    # 超过这个尺寸的图改走 ctx.images.upload() 换 URL part。
    inline_max_bytes: int = 256 * 1024
    # 动图（gif）刻意不走 upload：宿主会把任何输入归一成 JPEG，
    # 动画会被压平。动图只能内联，超过 inline_max_bytes 就如实拒绝。
    animated_via_upload: bool = False


@dataclass(frozen=True)
class StorageSettings:
    """表情库的容量参数。"""

    # 给模型看的目录最多列几条（按最近使用排序）。
    catalog_limit_for_model: int = 80
    # 使用台账保留多少条。
    usage_history_keep: int = 200


@dataclass(frozen=True)
class AwarenessSettings:
    """存在感注入（v0.2.0）的行为参数。

    与总开关的关系是**与**：`[sticker_manager].enabled=false` 时这里全不生效。
    节奏刻意保守——没有真机基线之前，宁可不注也不轰炸上下文。
    """

    # 子开关：默认开（总开关才是那道 fail-closed 闸）。
    enabled: bool = True
    # 同一角色卡两次存在感注入的最小间隔（秒）。默认一小时。
    interval_sec: float = 3600.0
    # 注入文本里"最近常用"最多带几行。
    max_recent_lines: int = 5


@dataclass(frozen=True)
class StickerManagerSettings:
    """插件总配置视图。"""

    # fail-closed 总开关：false = 不发图、不给模型目录，但库的增删改仍可用
    # （管理入口碰的是文件系统，不是行为链路）。
    enabled: bool = False
    send: SendSettings = field(default_factory=SendSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    awareness: AwarenessSettings = field(default_factory=AwarenessSettings)

    @classmethod
    def defaults(cls) -> "StickerManagerSettings":
        return cls(
            enabled=False,
            send=SendSettings(),
            storage=StorageSettings(),
            awareness=AwarenessSettings(),
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "StickerManagerSettings":
        section = config.get("sticker_manager") if isinstance(config, dict) else None
        if not isinstance(section, dict):
            return cls.defaults()
        send_raw = section.get("send") if isinstance(section.get("send"), dict) else {}
        storage_raw = (
            section.get("storage") if isinstance(section.get("storage"), dict) else {}
        )
        awareness_raw = (
            section.get("awareness") if isinstance(section.get("awareness"), dict) else {}
        )
        send_default = SendSettings()
        storage_default = StorageSettings()
        awareness_default = AwarenessSettings()
        return cls(
            enabled=_as_bool(section.get("enabled"), False),
            send=SendSettings(
                cooldown_sec=float(
                    _clamp_int(
                        _as_number(send_raw.get("cooldown_sec"), send_default.cooldown_sec),
                        0.0,
                        3600.0,
                    )
                ),
                inline_max_bytes=_clamp_int(
                    _as_int(send_raw.get("inline_max_bytes"), send_default.inline_max_bytes),
                    16 * 1024,
                    360 * 1024,
                ),
                animated_via_upload=_as_bool(
                    send_raw.get("animated_via_upload"), send_default.animated_via_upload
                ),
            ),
            storage=StorageSettings(
                catalog_limit_for_model=_clamp_int(
                    _as_int(
                        storage_raw.get("catalog_limit_for_model"),
                        storage_default.catalog_limit_for_model,
                    ),
                    5,
                    500,
                ),
                usage_history_keep=_clamp_int(
                    _as_int(
                        storage_raw.get("usage_history_keep"),
                        storage_default.usage_history_keep,
                    ),
                    20,
                    2000,
                ),
            ),
            awareness=AwarenessSettings(
                enabled=_as_bool(awareness_raw.get("enabled"), awareness_default.enabled),
                interval_sec=float(
                    _clamp_int(
                        _as_number(
                            awareness_raw.get("interval_sec"), awareness_default.interval_sec
                        ),
                        60.0,
                        86400.0,
                    )
                ),
                max_recent_lines=_clamp_int(
                    _as_int(
                        awareness_raw.get("max_recent_lines"),
                        awareness_default.max_recent_lines,
                    ),
                    1,
                    20,
                ),
            ),
        )


def _as_number(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _clamp_int(value: float | int, low: float | int, high: float | int) -> Any:
    return max(low, min(high, value))
