"""发送链路：把一张图落到她的聊天里，并记一次台账。

两条投递通道（由 `SendSettings` 决定走哪条）：
1. **内联**（默认，≤ `inline_max_bytes`）：`parts=[{"type":"image","data":bytes,"mime":...}]`。
   gif 一定走这条——宿主对输入图会归一成 JPEG，压平动画；内联保留原字节。
2. **上传换 URL**（大图 / 显式配置）：`await ctx.images.upload(data)` 拿回
   `{"type":"image","url":...,"mime":"image/jpeg"}` 的 part 再投递。

投递用 `visibility=["chat"]`（用户在聊天里看到图）+ `ai_behavior="read"`
（图进她的上下文但不额外起一轮 turn——发图这个动作本身就是她在说话）。
`submitted=True` 只代表已交给传输，不代表宿主已消费（our_life 陷阱 §5）。

冷却是**按角色卡的内存表**：不持久化。跨重启的发送节奏没必要留痕，
反而持久化会让"刚重启就误判冷却中"这种体验变糟。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..core.catalog import Sticker, detect_image_format
from ..core.configuration import StickerManagerSettings
from .library import Library

# 稳定错误码（模型/面板各自的文案层翻译它们）
ERR_NOT_ENABLED = "not_enabled"
ERR_MISSING_FILE = "sticker_file_missing"
ERR_BAD_IMAGE = "sticker_image_unreadable"
ERR_TOO_LARGE = "sticker_too_large"
ERR_COOLDOWN = "send_cooldown"
ERR_TRANSPORT = "transport_unavailable"


@dataclass
class SendResult:
    ok: bool
    code: str = ""
    detail: str = ""
    sticker_id: str = ""
    desc: str = ""

    @classmethod
    def failure(cls, code: str, *, sticker_id: str = "", detail: str = "") -> "SendResult":
        return cls(ok=False, code=code, sticker_id=sticker_id, detail=detail)

    @classmethod
    def success(cls, sticker: Sticker) -> "SendResult":
        return cls(ok=True, sticker_id=sticker.id, desc=sticker.desc)


class Sender:
    """发送器。`plugin` 提供 `ctx`（push_message / images）。"""

    def __init__(self, plugin: Any, library: Library, *, logger: Any = None):
        self._plugin = plugin
        self._library = library
        self._logger = logger
        self._last_sent: dict[str, float] = {}  # lanlan -> 上次成功投递的时刻

    def _log(self, message: str, *, exc: bool = False) -> None:
        if self._logger is None:
            return
        try:
            if exc:
                self._logger.warning(message, exc_info=True)
            else:
                self._logger.info(message)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 冷却
    # ------------------------------------------------------------------

    def cooldown_remaining(self, lanlan: str, settings: StickerManagerSettings, *, now: float) -> float:
        last = self._last_sent.get(lanlan)
        if last is None:
            return 0.0
        return max(0.0, settings.send.cooldown_sec - (now - last))

    # ------------------------------------------------------------------
    # 投递
    # ------------------------------------------------------------------

    async def send(
        self,
        sticker: Sticker,
        *,
        lanlan: str,
        settings: StickerManagerSettings,
        source: str,
        now: float | None = None,
    ) -> SendResult:
        """投递一张表情包并记账。source 只进台账（"tool" / "panel"），不面向用户。"""
        moment = time.time() if now is None else now
        if not settings.enabled:
            return SendResult.failure(ERR_NOT_ENABLED, sticker_id=sticker.id)
        if sticker.disabled:
            # 被主人禁用的图不该从任何通道漏出去。
            return SendResult.failure("sticker_disabled", sticker_id=sticker.id)
        remaining = self.cooldown_remaining(lanlan, settings, now=moment)
        if remaining > 0.0:
            return SendResult.failure(ERR_COOLDOWN, sticker_id=sticker.id)

        try:
            data = self._library.image_path(sticker).read_bytes()
        except FileNotFoundError:
            return SendResult.failure(ERR_MISSING_FILE, sticker_id=sticker.id)
        except Exception:
            return SendResult.failure(ERR_BAD_IMAGE, sticker_id=sticker.id)
        detected = detect_image_format(data)
        if detected is None:
            return SendResult.failure(ERR_BAD_IMAGE, sticker_id=sticker.id)
        _ext, mime = detected

        part = await self._build_part(data, mime, settings=settings)
        if part is None:
            return SendResult.failure(ERR_TOO_LARGE, sticker_id=sticker.id)

        pushed = self._push(part, lanlan=lanlan)
        if not pushed.get("submitted"):
            reason = str(pushed.get("reason", ERR_TRANSPORT))
            self._log(f"push rejected: reason={reason}")
            return SendResult.failure(reason or ERR_TRANSPORT, sticker_id=sticker.id)

        # 只有真交给传输了才前进冷却与计数（被拒的投递不该罚她等下一轮）。
        self._last_sent[lanlan] = moment
        self._library.touch_used(sticker.id, now=moment)
        self._library.append_usage(
            {
                "at": moment,
                "id": sticker.id,
                "lanlan": lanlan or "",
                "source": source,
                "ok": True,
            },
            keep=settings.storage.usage_history_keep,
        )
        self._log(f"sticker sent: id={sticker.id} source={source}")
        return SendResult.success(sticker)

    async def _build_part(
        self, data: bytes, mime: str, *, settings: StickerManagerSettings
    ) -> dict[str, Any] | None:
        """构造 push_message 的 image part；无法投递时返回 None。"""
        inline_budget = settings.send.inline_max_bytes
        if mime == "image/gif" and not settings.send.animated_via_upload:
            # gif 只能内联；内联不下就是真放不下（上传会毁掉动画，宁可拒绝）。
            if len(data) > inline_budget:
                return None
            return {"type": "image", "data": data, "mime": mime}
        if len(data) <= inline_budget:
            return {"type": "image", "data": data, "mime": mime}
        # 大图：换 URL part。
        try:
            upload = await self._plugin.ctx.images.upload(data, mime=mime, timeout=10.0)
        except Exception:
            self._log("image upload failed", exc=True)
            return None
        if isinstance(upload, dict) and upload.get("type") == "image":
            return dict(upload)
        return None

    def _push(self, part: dict[str, Any], *, lanlan: str) -> dict[str, Any]:
        ctx = self._plugin.ctx
        kwargs: dict[str, Any] = {
            "visibility": ["chat"],
            "ai_behavior": "read",
            "parts": [part],
            "description": "sticker_manager:send",
        }
        if lanlan:
            kwargs["target_lanlan"] = lanlan
        result = ctx.push_message(**kwargs)
        if isinstance(result, dict):
            return result
        return {"submitted": False, "reason": ERR_TRANSPORT}

    def note_attempt_failed(self, *, lanlan: str, sticker_id: str, code: str, settings, now: float) -> None:
        """失败的发送也进台账（ok=False），面板"她最近想用但没成"看得见。"""
        self._library.append_usage(
            {
                "at": now,
                "id": sticker_id,
                "lanlan": lanlan or "",
                "source": "tool",
                "ok": False,
                "code": code,
            },
            keep=settings.storage.usage_history_keep,
        )
