"""发送链路：把一张图落到她的聊天里，并记一次台账。

两条投递通道（由 `SendSettings` 决定走哪条）：
1. **内联**（默认，≤ `inline_max_bytes`）：`parts=[{"type":"image","data":bytes,"mime":...}]`。
   GIF/动画 WebP 默认走这条——上传会归一成 JPEG，压平动画；内联保留原字节。
2. **上传换 URL**（大图 / 显式配置）：`await ctx.images.upload(data)` 拿回
   `{"type":"image","url":...,"mime":"image/jpeg"}` 的 part 再投递。

投递用 `visibility=["chat"]`（用户在聊天里看到图）；工具选图无论即时还是排队都用
`ai_behavior="blind"`，避免自产配文与图片反灌到下一轮上下文；
面板与 Agent 路径保留 `ai_behavior="read"`。
`submitted=True` 只代表已交给传输，不代表宿主已消费（our_life 陷阱 §5）。

冷却是**按角色卡的内存表**：不持久化。跨重启的发送节奏没必要留痕，
反而持久化会让"刚重启就误判冷却中"这种体验变糟。

去重（轮 D①）与冷却相反，读的是**持久台账**（usage.json 里该角色卡最近
 N 张成功发送的 distinct id）："最近发过什么"是事实记忆，重启不该失忆。
概率闸门（轮 D②）掷后复用，判定缓存是内存表（重启重掷，与冷却同纪律）：
同一角色卡在复用窗口内只掷一次——multi_candidates→拿 id 二次定夺是同一次
意愿的延续，不重掷（外部系统的 p² 教训）。两者都拦 `source` 为 tool 或 agent 的模型路径：
面板"试发"是主人的直接动作，不该被她的行为节奏闸拦下。
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..core.catalog import Sticker, detect_image_format
from ..core.configuration import StickerManagerSettings
from ..core.media import preserves_animation
from .library import Library
from .official_delivery import OfficialDelivery
from .turn_end import EndTicket, TurnEndLogs

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
_TAIL_TIMEOUT_SEC = 300.0
_MAX_PENDING = 8


@dataclass
class SendResult:
    ok: bool
    code: str = ""
    detail: str = ""
    sticker_id: str = ""
    desc: str = ""
    queued: bool = False
    text_submitted: bool = False

    @classmethod
    def failure(
        cls, code: str, *, sticker_id: str = "", detail: str = "", text_submitted: bool = False,
    ) -> "SendResult":
        return cls(
            ok=False, code=code, sticker_id=sticker_id, detail=detail,
            text_submitted=text_submitted,
        )

    @classmethod
    def success(cls, sticker: Sticker, *, text_submitted: bool = False) -> "SendResult":
        return cls(
            ok=True, sticker_id=sticker.id, desc=sticker.desc, text_submitted=text_submitted,
        )


@dataclass
class PendingSend:
    sticker: Sticker
    parts: list[dict[str, Any]]
    ticket: EndTicket
    source: str
    started: float
    text_len: int
    display_buffer_sec: float
    end_observed_at: float | None = None


class Sender:
    """发送器。`plugin` 提供 `ctx`（push_message / images）。"""

    def __init__(
        self, plugin: Any, library: Library, *, logger: Any = None, turn_end: TurnEndLogs | None = None
    ):
        self._plugin = plugin
        self._library = library
        self._logger = logger
        self._last_sent: dict[str, float] = {}  # lanlan -> 上次成功投递的时刻
        self._prob_rolls: dict[str, tuple[float, bool]] = {}  # lanlan -> (掷骰时刻, 命中)
        self._inflight: set[str] = set()
        self._inflight_lock = threading.Lock()
        self._turn_end = turn_end
        self._pending: dict[str, PendingSend] = {}
        self._drain_lock = threading.Lock()
        self._closed = False
        self._epoch = 0
        self._official_delivery: OfficialDelivery | None = None

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
        if settings.send.cooldown_sec <= 0.0:
            return 0.0
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
        text: str = "",
    ) -> SendResult:
        """投递一张表情包并记账。source 为 tool / agent / panel，不面向用户。

        `text` 非空时先投配文消息，再投独立图片消息，仍共用一次排队、节奏判定与计数。
        先准备图片，准备失败时不投配文；传输没有事务或回滚，配文提交后图片仍可能失败，
        此时通过 `text_submitted` 如实报告部分提交，不重发，也不算发表情成功。
        两条消息署名仍是插件，不冒充她自己的回复气泡。
        """
        moment = time.time() if now is None else now
        # Reservation spans awaits and works across the host's separate event loops.
        with self._inflight_lock:
            if self._closed:
                return SendResult.failure("send_stopped", sticker_id=sticker.id)
            if lanlan in self._inflight:
                return SendResult.failure(ERR_COOLDOWN, sticker_id=sticker.id)
            self._inflight.add(lanlan)
            epoch = self._epoch
        handed_off = False
        try:
            result = await self._send_reserved(
                sticker, lanlan=lanlan, settings=settings, source=source, now=moment,
                force=force, text=text, epoch=epoch,
            )
            handed_off = result.queued
            return result
        finally:
            if not handed_off:
                with self._inflight_lock:
                    self._inflight.discard(lanlan)

    async def _send_reserved(
        self,
        sticker: Sticker,
        *,
        lanlan: str,
        settings: StickerManagerSettings,
        source: str,
        now: float,
        force: bool,
        text: str,
        epoch: int,
    ) -> SendResult:
        moment = now
        started = time.monotonic()
        if not settings.enabled:
            return SendResult.failure(ERR_NOT_ENABLED, sticker_id=sticker.id)
        if sticker.disabled:
            # 被主人禁用的图不该从任何通道漏出去。
            return SendResult.failure("sticker_disabled", sticker_id=sticker.id)
        remaining = self.cooldown_remaining(lanlan, settings, now=moment)
        if remaining > 0.0:
            return SendResult.failure(ERR_COOLDOWN, sticker_id=sticker.id)
        # Both model paths share the rhythm gates; panel sends keep their existing behavior.
        # （冷却仍生效——那是防刷屏，不是表达问题）。先查重（确定性）再掷骰，
        # 不让重复图白耗一次判定。
        if source in {"tool", "agent"} and not force:
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
        if len(data) > settings.send.inline_max_bytes:
            pack_path = getattr(self._plugin, "_official_pack_path", None)
            if callable(pack_path):
                try:
                    if self._official_delivery is None:
                        self._official_delivery = OfficialDelivery(pack_path())
                    variant = self._official_delivery.resolve(data)
                except Exception:
                    self._log("official delivery asset unavailable", exc=True)
                    return SendResult.failure(ERR_BAD_IMAGE, sticker_id=sticker.id)
                if variant is not None:
                    data = variant
                    mime = "image/webp"
                    self._log(f"official delivery variant: id={sticker.id} bytes={len(data)}")

        ticket = None
        if self._turn_end is not None and (
            source == "agent" or (source == "tool" and settings.send.defer_tool_sends)
        ):
            # Tool ordering is opt-in: log-based waiting can lose selections
            # on the next input. Background Agent requests retain their guard.
            defer = source == "tool"
            if source == "agent":
                states = await asyncio.to_thread(self._turn_end.busy_states)
                defer = states.get(lanlan) is not False
            if defer:
                ticket = self._turn_end.arm(lanlan)
                if ticket is None:
                    return SendResult.failure("turn_end_unavailable", sticker_id=sticker.id)

        part = await self._build_part(data, mime, settings=settings)
        if part is None:
            return SendResult.failure(ERR_TOO_LARGE, sticker_id=sticker.id)

        # 图片准备完成后才允许投配文；有配文的两条消息共用这次预约与排队。
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        parts.append(part)
        with self._inflight_lock:
            if self._closed:
                return SendResult.failure("send_stopped", sticker_id=sticker.id)
            if epoch != self._epoch:
                return SendResult.failure(ERR_NOT_ENABLED, sticker_id=sticker.id)
            if ticket is not None:
                if len(self._pending) >= _MAX_PENDING:
                    return SendResult.failure("send_queue_full", sticker_id=sticker.id)
                self._pending[lanlan] = PendingSend(
                    sticker, parts, ticket, source, time.monotonic(), len(text),
                    settings.send.reply_tail_display_buffer_sec,
                )
                self._log(
                    f"sticker queued: id={sticker.id} source={source} "
                    f"buffer_sec={settings.send.reply_tail_display_buffer_sec:g}"
                )
                return SendResult(ok=True, sticker_id=sticker.id, desc=sticker.desc, queued=True)

            moment += max(0.0, time.monotonic() - started)
            return self._submit(
                sticker, parts, lanlan=lanlan, settings=settings,
                source=source, moment=moment, text_len=len(text),
            )

    def _submit(
        self, sticker: Sticker, parts: list[dict[str, Any]], *, lanlan: str,
        settings: StickerManagerSettings, source: str, moment: float, text_len: int,
    ) -> SendResult:
        # Tool results already describe the selection. Reading our own image
        # again would make the next text-chat turn switch to a vision model.
        ai_behavior = "blind" if source == "tool" else "read"
        text_submitted = False
        for part in parts:
            partial = " text_submitted=True" if text_submitted else ""
            detail = "caption submitted; image not submitted" if text_submitted else ""
            try:
                pushed = self._push([part], lanlan=lanlan, ai_behavior=ai_behavior)
            except Exception:
                self._log(f"push failed{partial}", exc=True)
                return SendResult.failure(
                    ERR_TRANSPORT, sticker_id=sticker.id, detail=detail,
                    text_submitted=text_submitted,
                )
            if not pushed.get("submitted"):
                reason = str(pushed.get("reason", ERR_TRANSPORT))
                self._log(f"push rejected: reason={reason}{partial}")
                return SendResult.failure(
                    reason or ERR_TRANSPORT, sticker_id=sticker.id, detail=detail,
                    text_submitted=text_submitted,
                )
            if part["type"] == "text":
                text_submitted = True

        # 只有图片真交给传输了才前进冷却与计数，配文单独提交不算成功。
        self._last_sent[lanlan] = moment
        self._library.touch_used(sticker.id, now=moment)
        self._library.append_usage(
            {
                "at": moment,
                "id": sticker.id,
                "lanlan": lanlan or "",
                "source": source,
                "ok": True,
                # 配文只留长度痕：**只记长度不记内容**——那句是她生成的话，
                # 台账的既有纪律是"只放非隐私字段"（时刻/id/角色/来源/成败）。
                "text_len": text_len,
            },
            keep=settings.storage.usage_history_keep,
        )
        self._log(
            f"sticker sent: id={sticker.id} source={source} ai_behavior={ai_behavior}"
            + (f" text_len={text_len}" if text_len else "")
        )
        return SendResult.success(sticker, text_submitted=text_submitted)

    async def drain(self, *, settings: StickerManagerSettings) -> list[tuple[str, SendResult]]:
        """Buffer display after generation ends, watching for superseding input.

        The buffer is a configurable compatibility aid, not a frontend completion
        signal. Each timer tick continues to read new logs and the busy guard.
        """
        if self._turn_end is None or not self._drain_lock.acquire(blocking=False):
            return []
        outcomes: list[tuple[str, SendResult]] = []
        try:
            with self._inflight_lock:
                snapshot = list(self._pending.items())
            states = None
            for lanlan, pending in snapshot:
                code = ""
                ready = False
                if not settings.enabled:
                    code = ERR_NOT_ENABLED
                elif time.monotonic() - pending.started >= _TAIL_TIMEOUT_SEC:
                    code = "turn_end_timeout"
                else:
                    ready = await asyncio.to_thread(self._turn_end.poll, pending.ticket)
                    if pending.ticket.matched and pending.end_observed_at is None:
                        pending.end_observed_at = time.monotonic()
                        self._log(
                            f"sticker tail observed: id={pending.sticker.id} "
                            f"buffer_sec={pending.display_buffer_sec:g}"
                        )
                    if pending.ticket.invalid:
                        code = "turn_end_lost"
                    elif pending.ticket.superseded:
                        code = "turn_superseded"
                    elif pending.ticket.matched:
                        if states is None:
                            states = await asyncio.to_thread(self._turn_end.busy_states)
                        busy = states.get(lanlan)
                        if busy is True:
                            code = "turn_superseded"
                        elif time.monotonic() - pending.end_observed_at < pending.display_buffer_sec:
                            ready = False
                with self._inflight_lock:
                    if self._pending.get(lanlan) is not pending:
                        continue
                    if not code:
                        loaded = self._library.load()
                        sticker = self._library.get(pending.sticker.id)
                        if not loaded.ok:
                            code = loaded.code
                        elif sticker is None or not self._library.image_path(sticker).is_file():
                            code = ERR_MISSING_FILE
                        elif sticker.disabled:
                            code = "sticker_disabled"
                        elif sticker.id not in {s.id for s in self._library.active_pool()}:
                            code = "sticker_zone_changed"
                    if not code and not ready:
                        continue
                    try:
                        if code:
                            result = SendResult.failure(code, sticker_id=pending.sticker.id)
                        else:
                            result = self._submit(
                                sticker, pending.parts, lanlan=lanlan, settings=settings,
                                source=pending.source, moment=time.time(), text_len=pending.text_len,
                            )
                        if not result.ok:
                            self.note_attempt_failed(
                                lanlan=lanlan, sticker_id=pending.sticker.id, code=result.code,
                                settings=settings, now=time.time(), source=pending.source,
                                text_submitted=result.text_submitted,
                            )
                            end_wait = (
                                "no-end" if pending.end_observed_at is None
                                else f"{max(0.0, time.monotonic() - pending.end_observed_at):.2f}"
                            )
                            self._log(
                                f"sticker deferred rejected: id={pending.sticker.id} "
                                f"reason={result.code} end_wait_sec={end_wait}"
                                + (" text_submitted=True" if result.text_submitted else "")
                            )
                        outcomes.append((pending.source, result))
                    finally:
                        self._pending.pop(lanlan, None)
                        self._inflight.discard(lanlan)
            return outcomes
        finally:
            self._drain_lock.release()

    def cancel_pending(
        self, *, settings: StickerManagerSettings, closing: bool = False
    ) -> list[tuple[str, SendResult]]:
        outcomes = []
        with self._inflight_lock:
            self._closed = self._closed or closing
            self._epoch += 1
            for lanlan, pending in list(self._pending.items()):
                code = "send_stopped" if closing else ERR_NOT_ENABLED
                self.note_attempt_failed(
                    lanlan=lanlan, sticker_id=pending.sticker.id, code=code,
                    settings=settings, now=time.time(), source=pending.source,
                )
                outcomes.append((pending.source, SendResult.failure(code, sticker_id=pending.sticker.id)))
                self._inflight.discard(lanlan)
            self._pending.clear()
        return outcomes

    async def _build_part(
        self, data: bytes, mime: str, *, settings: StickerManagerSettings,
    ) -> dict[str, Any] | None:
        """构造 push_message 的 image part；无法投递时返回 None。

        配文是独立消息，不占用图片的内联预算。
        """
        inline_budget = settings.send.inline_max_bytes
        if preserves_animation(data, mime) and not settings.send.animated_via_upload:
            # GIF and animated WebP must keep their original bytes.
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

    def _push(
        self, parts: list[dict[str, Any]], *, lanlan: str, ai_behavior: str = "read"
    ) -> dict[str, Any]:
        ctx = self._plugin.ctx
        kwargs: dict[str, Any] = {
            "visibility": ["chat"],
            "ai_behavior": ai_behavior,
            "parts": parts,
            "description": "sticker_manager:send",
        }
        if lanlan:
            kwargs["target_lanlan"] = lanlan
        result = ctx.push_message(**kwargs)
        if isinstance(result, dict):
            return result
        return {"submitted": False, "reason": ERR_TRANSPORT}

    def note_attempt_failed(
        self, *, lanlan: str, sticker_id: str, code: str, settings, now: float,
        source: str = "tool", text_submitted: bool = False,
    ) -> None:
        """失败的发送也进台账（ok=False），面板"她最近想用但没成"看得见。"""
        entry = {
            "at": now,
            "id": sticker_id,
            "lanlan": lanlan or "",
            "source": source,
            "ok": False,
            "code": code,
        }
        if text_submitted:
            entry["text_submitted"] = True
        self._library.append_usage(entry, keep=settings.storage.usage_history_keep)
