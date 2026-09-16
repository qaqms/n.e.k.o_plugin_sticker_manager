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

去重（轮 D①）与冷却相反，读的是**持久台账**（usage.json 里该角色卡最近
 N 张成功发送的 distinct id）："最近发过什么"是事实记忆，重启不该失忆。
概率闸门（轮 D②）掷后复用，判定缓存是内存表（重启重掷，与冷却同纪律）：
同一角色卡在复用窗口内只掷一次——multi_candidates→拿 id 二次定夺是同一次
意愿的延续，不重掷（外部系统的 p² 教训）。两者都只拦 `source=="tool"`：
面板"试发"是主人的直接动作，不该被她的行为节奏闸拦下。
"""

from __future__ import annotations

import random
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
# 轮 D 新增：两个节奏闸的稳定码（进台账、面板可翻译）。
ERR_RECENT_REPEAT = "recent_repeat"
ERR_PROBABILITY = "probability_declined"

# 掷骰用模块级实例：测试里 monkeypatch `sender._RNG` 即可钉死随机序列。
_RNG = random.Random()


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
        self._prob_rolls: dict[str, tuple[float, bool]] = {}  # lanlan -> (掷骰时刻, 命中)

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
    # 节奏闸（轮 D）
    # ------------------------------------------------------------------

    def recent_sent_ids(self, lanlan: str, settings: StickerManagerSettings) -> set[str]:
        """该角色卡最近 `recent_dedup_count` 张**成功发出**的 distinct id（台账源，跨重启）。

        读整本台账（封顶 usage_history_keep，文件很小）再筛：ok=True 且角色卡匹配，
        新的在前，取前 N 个 distinct id。一次成功发送只占一个名额（同一张连发两次
        不挤掉更早的另一张）。
        """
        n = settings.send.recent_dedup_count
        if n <= 0:
            return set()
        out: set[str] = set()
        for row in self._library.read_usage(limit=settings.storage.usage_history_keep):
            if not row.get("ok"):
                continue
            if str(row.get("lanlan") or "") != lanlan:
                continue
            sid = str(row.get("id") or "")
            if sid:
                out.add(sid)
                if len(out) >= n:
                    break
        return out

    def probability_allow(self, lanlan: str, settings: StickerManagerSettings, *, now: float) -> bool:
        """概率闸门：掷一次、缓存、复用窗口内不重掷（防 p²）。

        `probability >= 1.0` 短路放行（默认关闭，不白耗随机）；判定缓存是内存表，
        重启重掷——与冷却同纪律（DESIGN 陷阱 9 的适用面就是节奏状态，去重不在内）。
        """
        p = settings.send.probability
        if p >= 1.0:
            return True
        rolled = self._prob_rolls.get(lanlan)
        if rolled is not None and (now - rolled[0]) < settings.send.probability_reuse_sec:
            return rolled[1]
        hit = _RNG.random() < p
        self._prob_rolls[lanlan] = (now, hit)
        return hit

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
        force: bool = False,
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
        # 两个节奏闸只拦她（source=="tool"）；force=主人点名要再看这张，绕行两个闸
        # （冷却仍生效——那是防刷屏，不是表达问题）。先查重（确定性）再掷骰，
        # 不让重复图白耗一次判定。
        if source == "tool" and not force:
            if sticker.id in self.recent_sent_ids(lanlan, settings):
                return SendResult.failure(ERR_RECENT_REPEAT, sticker_id=sticker.id)
            if not self.probability_allow(lanlan, settings, now=moment):
                return SendResult.failure(ERR_PROBABILITY, sticker_id=sticker.id)

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

    async def _build_part(self, data: bytes, mime: str, *, settings: StickerManagerSettings) -> dict[str, Any] | None:
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
