"""存在感注入的有状态层（v0.2.0）：什么时候注、注给谁、怎么兜底。

链路（挂在已有的 60s `on_watch` 拍上，不新增表——与 tool_watch 同拍各查各的）：

1. **给谁**：读 `bus.conversations` 找"最近有轮次的角色卡"。没人聊天就不注——
   往不存在的对话里注提示是纯浪费；`conversations` 不支持 watch()（宿主坑 2），
   低频轮询只读快照是 our_life 验证过的唯一形态。
2. **什么时候**：按角色卡的内存时钟（`interval_sec`，默认 3600s）。重启清零
   与发送冷却同一纪律（DESIGN 陷阱 9）：提示节奏不值得持久化，"刚重启就注一条"
   反而自然（她正需要想起自己有什么）。
3. **注什么**：core/awareness.build_awareness_text（库大小 + 套图分类概览 + 最近常用前 N 行）。
   空库返回空串 = 这拍不该注。
4. **怎么注**：`push_message(visibility=[], ai_behavior="read")`——用户看不见、
   不打断话轮，只进她的上下文（our_life injector 同通道，timer 里可用已被验证）。

三条纪律（与 tool_watch 逐字对齐的形态）：
- **永不炸拍**：maybe_run 吞掉一切异常回 `{"status": "failed"}`——
  timer 无 watchdog，注入坏掉不许连带心跳、更不许把表标黄；
- **随业务冻结**：`[sticker_manager].enabled=false` 时不注（存在感是行为链路，
  与"注册韧性是在场性"不同，这里就该跟着总开关联动）；
- **被拒不推进时钟**：`submitted=False`（本地拦截）时下一拍重试同一条不算轰炸，
  真推进时钟只发生在交到传输之后（与 sender 的冷却前进条件对偶）。

跨仓重复声明：`_records_of` / `_active_lanlan` 的记录规范化形状抄自 our_life 的
sampler（插件碰不到别人的 services，这是刻意的跨仓重复——两处都要改时用同一份测试口径）。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from typing import Any

from ..core.awareness import build_awareness_text
from .library import Library

logger = logging.getLogger("sticker_manager.awareness")

# 快照读多少条：只为找"最近在跟谁说话"，不必翻整本历史。
_SCAN_RECORDS = 12


def _normalize(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _records_of(raw: Any) -> list[Mapping[str, Any]]:
    """把 SDK 的 bus 列表对象规范成 dict 列表（兼容 dump_records / items / 可迭代）。"""
    if raw is None:
        return []
    dumper = getattr(raw, "dump_records", None)
    if callable(dumper):
        try:
            return _normalize(dumper())
        except Exception:  # noqa: BLE001 - 形状不认识就当没有
            pass
    items = getattr(raw, "items", None)
    if isinstance(items, (list, tuple)):
        return _normalize(items)
    if isinstance(raw, (list, tuple)):
        return _normalize(raw)
    return []


def lanlan_of(record: Mapping[str, Any]) -> str:
    """一条轮次记录里的角色卡名（顶层或 metadata 里，取到即止）。"""
    name = record.get("lanlan_name")
    if not isinstance(name, str) or not name.strip():
        metadata = record.get("metadata")
        meta: Mapping[str, Any] = metadata if isinstance(metadata, Mapping) else {}
        name = meta.get("lanlan_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return ""


def latest_lanlan(records: Iterable[Mapping[str, Any]]) -> str:
    """纯函数：从轮次记录里挑"时间戳最新的角色卡"；没有就回空串。

    时间戳缺失/坏值当 0（垫底）——有比没有好，但不能让坏数据插队到"最新"。
    """
    best_name = ""
    best_ts = -math.inf  # 常量哨兵；不用 float("-inf") 写法——字面即意，也不招扫描器误报
    for record in records:
        if not isinstance(record, Mapping):
            continue
        name = lanlan_of(record)
        if not name:
            continue
        raw_ts = record.get("timestamp")
        # 与 core._lenient_float 同纪律：isinstance 拦住字符串，但拦不住巨整数——
        # float(10**400) 抬 OverflowError；坏时间戳当 0 垫底，不能炸掉整拍扫描。
        if isinstance(raw_ts, (int, float)) and not isinstance(raw_ts, bool):
            try:
                ts = float(raw_ts)
            except OverflowError:
                ts = 0.0
        else:
            ts = 0.0
        if ts > best_ts:
            best_ts, best_name = ts, name
    return best_name


class Awareness:
    """低频存在感注入器。`maybe_run` 每拍调用，内部按角色卡时钟自节流。"""

    def __init__(
        self,
        plugin: Any,
        library: Library,
        *,
        logger: Any = None,
    ):
        self._plugin = plugin
        self._library = library
        self._logger = logger
        self._last_injected: dict[str, float] = {}
        # 面板可观测面（纯展示，丢了不心疼）：
        self.last_status = ""
        self.last_inject_at = 0.0
        self.last_target = ""

    # ------------------------------------------------------------------
    # 时钟与快照
    # ------------------------------------------------------------------

    def remaining_for(self, lanlan: str, *, interval_sec: float, now: float) -> float:
        last = self._last_injected.get(lanlan)
        if last is None:
            return 0.0
        return max(0.0, interval_sec - (now - last))

    def snapshot(self, *, interval_sec: float, now: float) -> dict[str, Any]:
        """给面板的状态行：最近一次注给谁、什么时候、下次最快多久以后。"""
        return {
            "status": self.last_status,
            "target": self.last_target,
            "last_inject_at": self.last_inject_at or None,
            "min_next_wait_sec": min(
                (self.remaining_for(k, interval_sec=interval_sec, now=now) for k in self._last_injected),
                default=0.0,
            ),
        }

    # ------------------------------------------------------------------
    # 节拍
    # ------------------------------------------------------------------

    async def maybe_run(self, *, settings: Any, now: float) -> dict[str, Any]:
        """自动一拍。永不抛异常（纪律见模块 docstring），结果只用于可观测。"""
        try:
            return await self._run(settings=settings, now=now, force=False)
        except Exception:  # noqa: BLE001 - timer 无 watchdog，一切异常就地消化
            self._log("sticker_manager awareness leaked", exc=True)
            return {"status": "failed"}

    async def inject_now(self, *, settings: Any, lanlan: str, now: float) -> dict[str, Any]:
        """手动一拍（面板调试入口）：绕过间隔闸，其余闸一个不少。"""
        return await self._run(settings=settings, now=now, force=True, lanlan_hint=lanlan)

    async def _run(self, *, settings: Any, now: float, force: bool, lanlan_hint: str = "") -> dict[str, Any]:
        if not settings.enabled or not settings.awareness.enabled:
            return {"status": "disabled"}
        target = (lanlan_hint or "").strip() or await self._active_lanlan()
        if not target:
            # 没有可归属的角色卡：不推进任何时钟，也不报错——没人说话就没地方注。
            return {"status": "no_target"}
        if not force:
            waiting = self.remaining_for(target, interval_sec=settings.awareness.interval_sec, now=now)
            if waiting > 0.0:
                return {"status": "waiting", "wait_sec": round(waiting, 1)}
        text = build_awareness_text(
            self._library.active_pool(),
            # J-1：存在感只报她当前世界（激活区）的家底，不报跨区总量。
            max_lines=settings.awareness.max_recent_lines,
            groups=self._library.group_descs(),
        )
        if not text:
            return {"status": "empty_library"}
        pushed = self._push(text, target)
        if not pushed.get("submitted"):
            reason = str(pushed.get("reason", "push_rejected"))
            self._log(f"awareness push rejected: reason={reason}")
            return {"status": "push_rejected", "reason": reason}
        self._last_injected[target] = now
        self.last_status = "injected"
        self.last_inject_at = now
        self.last_target = target
        self._log(f"awareness injected: target={target} chars={len(text)}")
        return {"status": "injected", "target": target, "chars": len(text)}

    # ------------------------------------------------------------------
    # 宿主通道（全部 getattr 化：测试替身/形状缺失一律降级不炸）
    # ------------------------------------------------------------------

    async def _active_lanlan(self) -> str:
        namespace = getattr(getattr(self._plugin, "bus", None), "conversations", None)
        getter = getattr(namespace, "get", None)
        if not callable(getter):
            return ""
        try:
            raw: Any = getter(
                max_count=_SCAN_RECORDS
            )  # 宿主/替身可能给同步列表或 awaitable；鸭子形状 Any 化，下面的 hasattr 尺把关
            if hasattr(raw, "__await__"):
                raw = await raw
        except Exception:  # noqa: BLE001 - 总线读失败 = 这拍没有目标，不是事故
            self._log("bus.conversations.get failed", exc=True)
            return ""
        return latest_lanlan(_records_of(raw))

    def _push(self, text: str, lanlan: str) -> dict[str, Any]:
        ctx = getattr(self._plugin, "ctx", None)
        push = getattr(ctx, "push_message", None)
        if not callable(push):
            return {"submitted": False, "reason": "transport_unavailable"}
        result = push(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": text}],
            target_lanlan=lanlan,
            description="sticker_manager:awareness",
        )
        return result if isinstance(result, dict) else {"submitted": False}

    def _log(self, message: str, *, exc: bool = False) -> None:
        target = self._logger if self._logger is not None else logger
        try:
            if exc:
                target.warning(message, exc_info=True)
            else:
                target.info(message)
        except Exception:  # noqa: BLE001 - 日志面坏掉不许影响注入
            pass
