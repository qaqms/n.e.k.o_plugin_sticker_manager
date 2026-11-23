"""Plugin-owned image selection using the host's original-reply callback."""
from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import replace
from typing import Any

from ..core.attachment_selection import (
    MODEL_USAGE,
    PERSISTENT_MODEL_FAILURES,
    REPLY_SELECTION_PROMPT,
    model_error_status,
    parse_reply_selection,
    request_rejected,
    selection_candidates,
    selection_payload,
)
from .local_http import read_local_json

ENTRY = "after_reply"
SELECTION_TIMEOUT = 12.0
SELECTION_MAX_TOKENS = 4096
# Consecutive generic gateway failures before chat tools take over sending.
TRANSIENT_FAILURE_LIMIT = 3
# While chat tools send, re-check the selection model this often (seconds).
FALLBACK_RETRY_SEC = 600.0
# Statuses that prove the selection model answered.
_MODEL_ANSWERED = frozenset({
    "selected", "registered", "registration_uncertain", "submitted", "no_match", "text_only",
    "serious_task", "model_truncated", "invalid_result", "superseded",
})
_TEXT_ONLY = (
    "不要发图", "不要表情", "不发表情", "别发图", "不配图", "纯文字", "只用文字",
    "只回复文字", "no images", "text only", "no stickers", "text-only",
)
# How often to pick is decided before the model call (send.probability), so the
# prompt only judges fit, as in the reference meme manager's selector mode.
_SELECTION_GUIDANCE = "本轮需要判断是否配图：有贴切的图就选一张；候选都不贴切、会歪曲原意时跳过。"
_RNG = random.SystemRandom()
# Start the answer for the model. Reasoning models such as the host's free
# familiar model otherwise think for hundreds of tokens (often past the host's
# 15 s callback limit); continuing from this prefix they answer in ~2 tokens.
# Providers that ignore the prefix still answer with the whole JSON object.
ANSWER_PREFIX = '{"choice":'


class ReplyCompletedAttachment:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self.mode = "legacy"
        self.status = "idle"
        self._instance = None
        self._watch_lock = threading.Lock()
        self._lock = threading.RLock()
        self._epoch = 0
        self._closed = False
        self._tasks = {}
        self._seen = OrderedDict()
        self._gaps = {}
        self._history = OrderedDict()
        self.received = self.attempts = self.sent = self.registered = 0
        self.legacy_tools = {}
        self.last_model_result = {}
        # Set when the selection model keeps failing; maintain() then hands
        # sending back to chat tools ("fallback") and retries the model later.
        self.fallback_reason = ""
        self._transient_failures = 0
        self._retry_at = 0.0
        self._probe_passed = False
        # Cleared for this process when the bound provider rejects a request
        # that ends with the assistant answer prefix.
        self._prefill = True

    @property
    def managed(self):
        """True while the reply hook owns sending and chat tools are withdrawn."""
        return self.mode in {"pending", "hook"}

    def snapshot(self):
        with self._lock:
            return {
                "mode": self.mode, "status": self.status, "received": self.received,
                "attempts": self.attempts, "sent": self.sent, "registered": self.registered,
                "reply_gap": max(self._gaps.values(), default=0),
                "model_result": dict(self.last_model_result),
                "fallback_reason": self.fallback_reason,
            }

    def invalidate(self, *, closing=False):
        with self._lock:
            self._epoch += 1
            self._closed = self._closed or closing
            self._gaps.clear()
            self._history.clear()
            for loop, task in list(self._tasks.values()):
                if not loop.is_closed():
                    loop.call_soon_threadsafe(task.cancel)

    def _note(self, status):
        with self._lock:
            self.status = status
        return {"status": status}

    async def _select(self, text, history, rows, settings):
        prompt = REPLY_SELECTION_PROMPT
        if settings.send.every_reply:
            prompt += "每轮配图：普通聊天优先选一张最接近的简单表情；只有候选全都冲突时才 no_match。"
        else:
            prompt += _SELECTION_GUIDANCE
        started = time.monotonic()
        payload = json.dumps(
            selection_payload(text, history, rows), ensure_ascii=False, separators=(",", ":"),
        )
        self.last_model_result = {
            "candidate_count": len(rows), "max_tokens": SELECTION_MAX_TOKENS,
            "input_chars": len(prompt) + len(payload),
        }
        try:
            async with asyncio.timeout(SELECTION_TIMEOUT):
                client = await self.plugin.ctx.models.get_client()
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": payload},
                ]
                prefilled = self._prefill
                try:
                    response = await client.chat.completions.create(
                        model=MODEL_USAGE, stream=False, max_tokens=SELECTION_MAX_TOKENS,
                        messages=[*messages, {"role": "assistant", "content": ANSWER_PREFIX}] if prefilled else messages,
                    )
                except Exception as exc:
                    if not (prefilled and request_rejected(exc)):
                        raise
                    # The provider refuses a trailing assistant message; ask
                    # plainly from now on (slower on reasoning models).
                    self._prefill = prefilled = False
                    self.last_model_result["prefill"] = "rejected"
                    response = await client.chat.completions.create(
                        model=MODEL_USAGE, stream=False, max_tokens=SELECTION_MAX_TOKENS, messages=messages,
                    )
            choices = getattr(response, "choices", None)
            choice = choices[0] if choices else None
            content = getattr(getattr(choice, "message", None), "content", None)
            self.last_model_result.setdefault("prefill", "used" if prefilled else "off")
            finish = getattr(choice, "finish_reason", None)
            usage = getattr(response, "usage", None)
            details = getattr(usage, "completion_tokens_details", None)
            reasoning_tokens = getattr(details, "reasoning_tokens", None)
            reasoning_content = getattr(getattr(choice, "message", None), "reasoning_content", None)
            self.last_model_result.update({
                "finish_reason": finish if finish in {"stop", "length", "tool_calls", "content_filter"} else "unknown",
                "output_chars": len(content) if isinstance(content, str) else 0,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "reasoning_tokens": reasoning_tokens if type(reasoning_tokens) is int and reasoning_tokens >= 0 else None,
                "has_reasoning": isinstance(reasoning_content, str) and bool(reasoning_content),
                "output_format": (
                    "json" if isinstance(content, str) and content.lstrip().startswith("{")
                    else "fenced_json" if isinstance(content, str) and content.lstrip().startswith("```")
                    else "continuation" if prefilled and isinstance(content, str) and content.strip()
                    else "other"
                ),
            })
            if finish == "length":
                return "", "model_truncated"
            if finish != "stop":
                return "", "invalid_result"
            if prefilled and isinstance(content, str) and not content.lstrip().startswith(("{", "```")):
                # The model continued our prefix; parse the whole object.
                content = ANSWER_PREFIX + content
            return parse_reply_selection(content, rows)
        finally:
            self.last_model_result["duration_ms"] = round((time.monotonic() - started) * 1000)

    async def test_selection(self):
        """Probe the bound usage with synthetic text; never submit an image."""
        if self._tasks:
            return {"status": "busy"}
        if not self.plugin._library.load().ok:
            return {"status": "library_unavailable"}
        text = "太好了！听到这个消息我也很开心！"
        rows = selection_candidates(
            self.plugin._library.active_pool(), text, set(), self.plugin._library.group_descs(),
        )[:16]
        if not rows:
            return {"status": "empty_candidates"}
        try:
            sid, reason = await self._select(text, [
                {"role": "user_message", "text": "今天遇到了开心的事，想和你分享。"},
            ], rows, self.plugin._settings)
            status = "selected" if sid else reason
        except TimeoutError:
            status = "model_timeout"
        except Exception as exc:
            status = model_error_status(exc)
        with self._lock:
            if self.mode == "fallback" and status in _MODEL_ANSWERED:
                # The user fixed the model: resubscribe on the next watch tick.
                self._probe_passed, self._retry_at = True, 0.0
            elif self.mode == "hook":
                self._track_model(status)
        return {"status": status, **self.last_model_result}

    def _track_model(self, status):
        """Count model failures in hook mode; request fallback when they persist."""
        if status in _MODEL_ANSWERED:
            self._transient_failures = 0
        elif status in PERSISTENT_MODEL_FAILURES:
            self.fallback_reason = status
        elif status == "model_unavailable":
            self._transient_failures += 1
            if self._transient_failures >= TRANSIENT_FAILURE_LIMIT:
                self.fallback_reason = status

    async def _fall_back(self, client):
        """Unsubscribe and give sending back to chat tools; False if uncertain."""
        try:
            await client.unsubscribe(ENTRY)
        except Exception:
            # The host may still deliver callbacks; keep tools withdrawn and
            # retry on the next tick rather than risk two senders.
            return False
        reason = self.fallback_reason
        self.invalidate()
        with self._lock:
            self.mode, self._instance = "fallback", None
            self._transient_failures, self._probe_passed = 0, False
            self._retry_at = time.monotonic() + FALLBACK_RETRY_SEC
        self.plugin._eager_attachment.invalidate()
        self.plugin._refresh_send_tool_surface()
        # reason is one of this module's fixed status names, never provider text.
        self.plugin.logger.warning(
            f"sticker reply selection model unusable ({reason}); chat tools resume sending",
        )
        self._note(reason)
        return True

    async def _recovered(self):
        """In fallback, probe the model once the retry delay has passed."""
        if self._probe_passed:
            return True
        if time.monotonic() < self._retry_at:
            return False
        result = await self.test_selection()
        status = result.get("status")
        with self._lock:
            if status in _MODEL_ANSWERED:
                return True
            if status in PERSISTENT_MODEL_FAILURES or status == "model_unavailable":
                self.fallback_reason = status
            self._retry_at = time.monotonic() + FALLBACK_RETRY_SEC
            self.status = self.fallback_reason
        return False

    async def maintain(self):
        if self._closed or not self._watch_lock.acquire(blocking=False):
            return {"status": self.status}
        try:
            client = getattr(self.plugin.ctx, "reply_tail", None)
            if not callable(getattr(client, "subscribe", None)):
                self.mode = "legacy"
                return self._note("unsupported")
            if self.mode == "hook" and self.fallback_reason:
                await self._fall_back(client)
                return {"status": self.status}
            if self.mode == "fallback":
                if not await self._recovered():
                    return {"status": self.status}
                self.fallback_reason, self._probe_passed = "", False
                self.plugin.logger.info("sticker reply selection model answered; resubscribing")
            if not self.managed:
                self.mode = "pending"
                self.plugin._eager_attachment.invalidate()
            try:
                health = await asyncio.to_thread(
                    read_local_json, self.plugin._lanlan._api_base() + "/health", timeout=0.8,
                )
                instance = health.get("instance_id") if isinstance(health, dict) else None
                if self.mode == "hook" and instance and instance == self._instance:
                    return {"status": self.status}
                if self.mode == "hook" and not instance:
                    return {"status": self.status}
                if self.mode == "hook":
                    self.invalidate()
                    self.mode = "pending"
                receipt = await client.subscribe(ENTRY, handler_timeout=15.0)
            except Exception as exc:
                if getattr(getattr(exc, "response", None), "status_code", None) == 404:
                    self.mode = "legacy"
                    return self._note("unsupported")
                # A lost subscription response may have been accepted. Do not
                # activate a competing legacy sender on an uncertain result.
                return self._note("subscription_unavailable")
            if not isinstance(receipt, dict) or not receipt.get("ok"):
                return self._note("subscription_rejected")
            self.mode, self._instance = "hook", instance
            self.plugin._eager_attachment.invalidate()
            self.plugin._refresh_send_tool_surface()
            self.plugin.logger.info("sticker reply completion subscription active")
            return self._note("subscribed")
        finally:
            self._watch_lock.release()

    async def close(self):
        self.invalidate(closing=True)
        if self.managed:
            with suppress(Exception):
                await self.plugin.ctx.reply_tail.unsubscribe(ENTRY)

    def _valid(self, epoch):
        return not self._closed and self._epoch == epoch and self.mode == "hook" and self.plugin._settings.enabled

    async def handle(self, text, user_text, has_attachments, context):
        if (
            self.mode != "hook" or not isinstance(context, dict)
            or context.get("version") != 1
            or context.get("source") != "plugin:" + self.plugin.plugin_id
            or not isinstance(context.get("role"), str) or not context["role"]
            or not context.get("token") or not context.get("reply_id") or not context.get("request_id")
            or not isinstance(text, str) or not text.strip() or not isinstance(user_text, str)
        ):
            return self._note("invalid_context")
        role, identity = context["role"], (context["reply_id"], context["request_id"])
        # The next reply may complete before the one-second receipt timer.
        # Reconcile the previous submission before checking cooldown or busy.
        outcomes = await self.plugin._sender._drain_host(
            settings=self.plugin._settings, target_lanlan=role,
        )
        self.plugin._note_deferred_outcomes(outcomes)
        with self._lock:
            if self._closed or identity in self._seen:
                return {"status": "duplicate"}
            self._seen[identity] = None
            while len(self._seen) > 512:
                self._seen.popitem(last=False)
            self.received += 1
            history = self._history.setdefault(role, [])
            history.extend([{"role": "user_message", "text": user_text}, {"role": "assistant_message", "text": text}])
            del history[:-12]
            self._history.move_to_end(role)
            while len(self._history) > 32:
                old_role, _ = self._history.popitem(last=False)
                self._gaps.pop(old_role, None)
            history = list(history)
            if has_attachments:
                self._gaps[role] = 0
                return self._note("already_attached")
            if role in self._tasks:
                return {"status": "busy"}
            settings = self.plugin._settings
            if not self._valid(self._epoch):
                return self._note("inactive")
            if any(word in user_text.casefold() for word in _TEXT_ONLY):
                self._gaps[role] = 0
                return self._note("text_only")
            self._gaps[role] = self._gaps.get(role, 0) + 1
            if not settings.send.host_reply_tail:
                return self._note("reply_tail_disabled")
            if any(len(row["text"]) > 2000 for row in history) or sum(len(row["text"]) for row in history) > 10000:
                return self._note("context_too_large")
            if not settings.send.every_reply and self.plugin._sender.cooldown_remaining(role, settings, now=time.time()) > 0:
                return self._note("send_cooldown")
            version, busy, _ = self.plugin._sender.activity(role)
            if busy:
                return self._note("send_busy")
            # One roll per reply, before the model call: a miss costs nothing.
            probability = settings.send.probability
            if not settings.send.every_reply and probability < 1.0 and _RNG.random() >= probability:
                return self._note("probability_skip")
            epoch = self._epoch
            self._tasks[role] = (asyncio.get_running_loop(), asyncio.current_task())
        try:
            try:
                result = await self._choose(role, text, history, dict(context), settings, epoch, version)
            except TimeoutError:
                result = self._note("model_timeout")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = self._note(model_error_status(exc))
            with self._lock:
                self._track_model(result.get("status"))
            return result
        finally:
            self.plugin.logger.info(
                "sticker reply selection: status={} duration_ms={} finish_reason={} output_chars={} prefill={}",
                self.status, self.last_model_result.get("duration_ms", 0),
                self.last_model_result.get("finish_reason", ""), self.last_model_result.get("output_chars", 0),
                self.last_model_result.get("prefill", ""),
            )
            with self._lock:
                self._tasks.pop(role, None)

    async def _choose(self, role, text, history, context, settings, epoch, version):
        started = time.monotonic()
        library, sender = self.plugin._library, self.plugin._sender
        if not library.load().ok:
            return self._note("library_unavailable")
        zone = library.active_zone()
        rows = selection_candidates(
            library.active_pool(), text, sender.recent_sent_ids(role, settings), library.group_descs(),
            allow_recent=settings.send.every_reply,
        )[:16]
        if not rows:
            return self._note("empty_candidates")
        self.attempts += 1
        self._note("selecting")
        preparation_ms = round((time.monotonic() - started) * 1000)
        sid, reason = await self._select(text, history, rows, settings)
        self.last_model_result["preparation_ms"] = preparation_ms
        if not sid:
            if reason in {"text_only", "serious_task"}:
                self._gaps[role] = 0
            return self._note(reason)
        selected = next(row for row in rows if row["id"] == sid)

        def valid():
            current = library.get(sid)
            return (
                self._valid(epoch) and library.active_zone() == zone and current is not None
                and current.caption == selected["caption"] and current.desc == selected["description"]
                and current.visible_text == selected["visible_text"] and current.tags == selected["tags"]
                and current.group == selected["group"]
                and library.group_descs().get(current.group, "") == selected["group_description"]
            )

        with self._lock:
            if not valid():
                return self._note("superseded")
        # The probability was rolled before selecting; never roll it twice.
        send_settings = replace(settings, send=replace(
            settings.send, cooldown_sec=0.0, probability=1.0, recent_dedup_count=0,
        )) if settings.send.every_reply else replace(settings, send=replace(settings.send, probability=1.0))
        submitting = time.monotonic()
        result = await sender.send(
            library.get(sid), lanlan=role, settings=send_settings, source="eager", host_reply=context,
            expected_activity=version, submission_lock=self._lock, submission_guard=valid,
        )
        self.last_model_result["registration_ms"] = round((time.monotonic() - submitting) * 1000)
        if result.queued:
            self._gaps[role] = 0
            if result.detail == "host_reply_tail_uncertain":
                return self._note("registration_uncertain")
            self.registered += 1
            return self._note("registered")
        if not result.delivery_recorded:
            self.plugin._runstats.note_delivery(ok=result.ok, reason=result.code)
            result.delivery_recorded = True
        if result.ok:
            self.sent += 1
            self._gaps[role] = 0
        return self._note("submitted" if result.ok else result.code)

    def note_outcome(self, result):
        if result.ok:
            with self._lock:
                self.sent += 1
