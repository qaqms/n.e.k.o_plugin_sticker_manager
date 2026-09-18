"""存在感注入的有状态层（v0.2.0）：什么时候注、注给谁、怎么兜底。

链路（v0.16.0 起挂在 10s `turns` 拍上；60s `watch` 拍只管 tool_watch）：

1. **什么时候**：**由用户开的新一轮触发**（`services/turns.py` 轮询 `bus.memory` 的
   `user_message`），按 `inject_mode` 决定这轮注不注——每轮都注，或攒够 N 轮注一次；
   两种模式都吃 `min_interval_sec` 这把地板（连珠炮防刷屏）。
   老写法是挂钟 `interval_sec`（默认 3600s）打点，v0.15.0 实机量出来 44 轮对话只覆盖
   12 次提醒、3 次发表情：**瓶颈是提醒没赶上话轮，不是措辞不够狠**。挂钟只在总线一次
   都没读通过时作为降级路径保留（宿主换了桶形状也不许静默失声）。
2. **给谁**：轮次记录自带的归属角色（memory 桶的 `lanlan`）优先，其次
   `services/lanlan.py` 的解析链（入口 ctx → 宿主 `current_catgirl` → 粘滞 → 总线）。
   没人聊天就不注——往不存在的对话里注提示是纯浪费。
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
  真推进时钟/清计数只发生在交到传输之后（与 sender 的冷却前进条件对偶）。

目标是谁（她在跟哪张角色卡说话）不在本模块判：交给 `services/lanlan.py` 的解析链。
本模块只管节奏与投递。
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.awareness import build_awareness_text, injection_due_for_turn
from .lanlan import LanlanResolver
from .library import Library
from .turns import TurnWatcher, UserTurn

logger = logging.getLogger("sticker_manager.awareness")


class Awareness:
    """低频存在感注入器。`maybe_run` 每拍调用，内部按角色卡时钟自节流。"""

    def __init__(
        self,
        plugin: Any,
        library: Library,
        *,
        logger: Any = None,
        resolver: LanlanResolver | None = None,
        turns: TurnWatcher | None = None,
    ):
        self._plugin = plugin
        self._library = library
        self._logger = logger
        # 目标解析交给 LanlanResolver（入口 ctx → 宿主权威 → 粘滞 → 总线）；
        # 缺席时自建一个，形状与老调用点完全兼容。
        self._resolver = resolver or LanlanResolver(plugin, logger=logger)
        # v0.16.0：节奏的驱动源换成轮次（她每开一轮，我们才有一次决策点）。
        self._turns = turns if turns is not None else TurnWatcher(plugin, logger=logger)
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

    def snapshot(self, *, settings: Any, now: float) -> dict[str, Any]:
        """给面板的状态行：驱动源活着没、按什么节奏注、下次最快多久以后。"""
        awareness = settings.awareness
        return {
            "status": self.last_status,
            "target": self.last_target,
            "last_inject_at": self.last_inject_at or None,
            "driver": self._turns.snapshot()["source"],
            "inject_mode": awareness.inject_mode,
            "inject_interval_n": awareness.inject_interval_n,
            "turns_since_inject": self._turns.snapshot()["turns_since_inject"],
            "min_next_wait_sec": min(
                (
                    self.remaining_for(
                        k, interval_sec=awareness.min_interval_sec, now=now
                    )
                    for k in self._last_injected
                ),
                default=0.0,
            ),
        }

    # ------------------------------------------------------------------
    # 节拍
    # ------------------------------------------------------------------

    async def maybe_run(self, *, settings: Any, now: float) -> dict[str, Any]:
        """自动一拍：先问轮次源有没有新轮，再按模式决定注不注。

        永不抛异常（纪律见模块 docstring），结果只用于可观测。
        """
        try:
            turn = await self._turns.poll()
            return await self._run(settings=settings, now=now, force=False, turn=turn)
        except Exception:  # noqa: BLE001 - timer 无 watchdog，一切异常就地消化
            self._log("sticker_manager awareness leaked", exc=True)
            return {"status": "failed"}

    async def inject_now(self, *, settings: Any, lanlan: str, now: float) -> dict[str, Any]:
        """手动一拍（面板调试入口）：绕过节奏闸，其余闸一个不少。"""
        return await self._run(settings=settings, now=now, force=True, lanlan_hint=lanlan)

    async def _run(
        self,
        *,
        settings: Any,
        now: float,
        force: bool,
        lanlan_hint: str = "",
        turn: UserTurn | None = None,
    ) -> dict[str, Any]:
        if not settings.enabled or not settings.awareness.enabled:
            return {"status": "disabled"}
        awareness = settings.awareness
        target = (lanlan_hint or "").strip() or (turn.lanlan if turn is not None else "") or await self._active_lanlan()
        if not target:
            # 没有可归属的角色卡：不推进任何时钟，也不报错——没人说话就没地方注。
            return {"status": "no_target"}
        if not force:
            if turn is None and self._turns.available:
                # 轮次源活着但这拍没有新轮：什么都不做。挂钟模式那条"每小时注一次"
                # 的老路在这里正式退场——v0.15.0 实机 44 轮只覆盖 12 次提醒，
                # 就是这个"到点就注、不管她有没有在说话"造成的。
                return {"status": "idle"}
            if turn is not None:
                waiting_floor = self.remaining_for(
                    target, interval_sec=awareness.min_interval_sec, now=now
                )
                turns_since = self._turns.turns_since(target)
                if not injection_due_for_turn(
                    awareness.inject_mode,
                    turns_since_inject=turns_since,
                    interval_n=awareness.inject_interval_n,
                    floor_remaining_sec=waiting_floor,
                ):
                    return {"status": "not_due", "turns_since": turns_since}
            else:
                # 降级：总线一次都没读通过（宿主换了桶形状/总线断开），退回挂钟节奏，
                # 好过整条存在感链路静默死掉。
                waiting = self.remaining_for(target, interval_sec=awareness.interval_sec, now=now)
                if waiting > 0.0:
                    return {"status": "waiting", "wait_sec": round(waiting, 1)}
        text = build_awareness_text(
            self._library.active_pool(),
            # J-1：存在感只报她当前世界（激活区）的家底，不报跨区总量。
            max_lines=awareness.max_recent_lines,
            groups=self._library.group_descs(),
            # v0.14.0：意愿段按「配表情积极度」选档——档位只改这段文案，不碰发送层的闸。
            eagerness=settings.send.eagerness,
        )
        if not text:
            return {"status": "empty_library"}
        pushed = self._push(text, target)
        if not pushed.get("submitted"):
            reason = str(pushed.get("reason", "push_rejected"))
            self._log(f"awareness push rejected: reason={reason}")
            return {"status": "push_rejected", "reason": reason}
        self._last_injected[target] = now
        # 计数只在真注成功之后清零：被地板/空库挡掉的那些轮不消耗配额，
        # 否则"每 3 轮注一次"会静默退化成"每 4、5 轮注一次"。
        self._turns.reset_count(target)
        self.last_status = "injected"
        self.last_inject_at = now
        self.last_target = target
        self._log(
            f"awareness injected: target={target} chars={len(text)}"
            f" driver={'turn' if turn is not None else 'wall_clock'}"
        )
        return {"status": "injected", "target": target, "chars": len(text)}

    # ------------------------------------------------------------------
    # 宿主通道（全部 getattr 化：测试替身/形状缺失一律降级不炸）
    # ------------------------------------------------------------------

    async def _active_lanlan(self) -> str:
        return await self._resolver.resolve()

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
