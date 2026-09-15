"""存在感注入（v0.2.0）：core 纯函数 + services 节拍/时钟/闸。

口径钉四族：
1. 选内容与文案（core.awareness）：空库空串、行序=最近爱用、带上 sticker_send 指路；
2. 目标发现（latest_lanlan / bus 读失败降级）；
3. 节拍与闸：disabled / no_target / waiting / empty_library 各不推进时钟、不 push；
4. 被拒不推进时钟（下一拍重试同一条不算轰炸）+ 永不炸拍 + inject_now 只绕间隔。
"""

from __future__ import annotations

from typing import Any

from conftest import (
    PNG_BYTES,
    FakeBus,
    FakeConfig,
    FakeHostContext,
    build_plugin,
    conversation_record,
)
from sticker_manager.core.awareness import build_awareness_text, pick_recent
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import (
    AwarenessSettings,
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)
from sticker_manager.services.awareness import latest_lanlan


def _st(sid: str, *, desc: str = "", use: int = 0, last: float = 0.0, disabled: bool = False) -> Sticker:
    return Sticker(
        id=sid,
        file=f"{sid}.png",
        desc=desc or f"desc-{sid}",
        disabled=disabled,
        added_at=1000.0 + int(last),
        use_count=use,
        last_used_at=last,
    )


def _settings(*, enabled: bool = True, interval: float = 3600.0, lines: int = 5) -> StickerManagerSettings:
    return StickerManagerSettings(
        enabled=enabled,
        send=SendSettings(),
        storage=StorageSettings(),
        awareness=AwarenessSettings(enabled=True, interval_sec=interval, max_recent_lines=lines),
    )


class TestCoreSelection:
    def test_pick_recent_prefers_usage_then_recency(self):
        stickers = [_st("a", use=0, last=10), _st("b", use=3, last=5), _st("c", use=3, last=50)]
        picked = pick_recent(stickers, 2)
        assert [s.id for s in picked] == ["c", "b"]

    def test_pick_recent_skips_disabled_and_caps(self):
        stickers = [_st("a", use=9), _st("x", use=99, disabled=True), _st("b"), _st("c")]
        assert {s.id for s in pick_recent(stickers, 3)} == {"a", "b", "c"}
        assert all(s.id != "x" for s in pick_recent(stickers, 10))

    def test_empty_library_gives_empty_text(self):
        assert build_awareness_text([], max_lines=5) == ""
        assert build_awareness_text([_st("x", disabled=True)], max_lines=5) == ""

    def test_text_carries_count_lines_and_nudge(self):
        stickers = [_st("a", desc="猫猫挥手", use=2), _st("b", desc="大哭", use=1), _st("c")]
        text = build_awareness_text(stickers, max_lines=2)
        assert "3" in text  # 总数按未禁用算
        assert "猫猫挥手" in text and "大哭" in text
        assert "[a]" in text  # 行形状与 sticker_list 对偶：id 可拿去 sticker_send
        assert "sticker_send" in text

    def test_text_carries_usage_guidance_and_candidate_hint(self):
        # 轮 C：注入文案带"使用规则+数量软提示"（区分安慰/自述、宁缺毋滥）
        # 与候选机制告知（多候选回清单），软提示在文案、硬闸在冷却——两层分离。
        text = build_awareness_text([_st("a", desc="笑", use=1)], max_lines=5)
        assert "安慰对方" in text and "宁缺毋滥" in text
        assert "候选" in text and "id" in text

    def test_max_lines_caps_the_block(self):
        stickers = [_st(f"s{i}", use=i) for i in range(10)]
        text = build_awareness_text(stickers, max_lines=3)
        assert text.count("[s") == 3


class TestLatestLanlan:
    def test_picks_newest_record(self):
        records = [
            conversation_record("c1", 100.0, "K"),
            conversation_record("c2", 300.0, "M"),
            conversation_record("c3", 200.0, "K"),
        ]
        assert latest_lanlan(records) == "M"

    def test_bad_timestamps_sink_to_bottom(self):
        records = [
            conversation_record("c1", 500.0, "Good"),
            {"conversation_id": "c2", "metadata": {"lanlan_name": "NoTs"}},
            {"conversation_id": "c3", "timestamp": True, "metadata": {"lanlan_name": "BoolTs"}},
        ]
        assert latest_lanlan(records) == "Good"

    def test_records_without_names_or_empty_give_blank(self):
        assert latest_lanlan([]) == ""
        assert latest_lanlan([{"conversation_id": "x"}]) == ""
        assert latest_lanlan([{"metadata": {"lanlan_name": "  "}}]) == ""

    def test_top_level_lanlan_field_also_counts(self):
        assert latest_lanlan([{"timestamp": 1.0, "lanlan_name": "Top"}]) == "Top"


class TestAwarenessGates:
    def test_master_switch_freezes_injection(self, tmp_path, run_async):
        # 与 tool_watch 相反的存在感纪律：业务关 = 注入门上（docstring 纪律 2）。
        host = FakeHostContext(
            config=FakeConfig(data={}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 10.0, "K")]),
        )
        plugin, host = build_plugin(host)
        result = run_async(plugin._awareness.maybe_run(settings=_settings(enabled=False), now=100.0))
        assert result["status"] == "disabled"
        assert host.push.calls == []

    def test_no_conversation_target_skips(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=100.0))
        assert result["status"] == "no_target"
        assert host.push.calls == []

    def test_empty_library_skips_without_clock(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 10.0, "K")]),
        )
        plugin, host = build_plugin(host)
        result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=100.0))
        assert result["status"] == "empty_library"
        assert host.push.calls == []
        # 库后来有了内容：下一拍就能注（时钟没被空拍推进）。
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        second = run_async(plugin._awareness.maybe_run(settings=_settings(), now=101.0))
        assert second["status"] == "injected"

    def test_injects_once_then_waits_on_interval(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 10.0, "K")]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="猫猫挥手", tags=[])
        first = run_async(plugin._awareness.maybe_run(settings=_settings(interval=100.0), now=50.0))
        assert first["status"] == "injected"
        soon = run_async(plugin._awareness.maybe_run(settings=_settings(interval=100.0), now=120.0))
        assert soon["status"] == "waiting"
        late = run_async(plugin._awareness.maybe_run(settings=_settings(interval=100.0), now=151.0))
        assert late["status"] == "injected"

    def test_push_shape_is_silent_read_directed_to_card(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 10.0, "K")]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="猫猫挥手", tags=[])
        run_async(plugin._awareness.maybe_run(settings=_settings(), now=50.0))
        assert len(host.push.calls) == 1
        call = host.push.calls[0]
        assert call["visibility"] == []  # 用户看不见
        assert call["ai_behavior"] == "read"  # 不起话轮
        assert call["target_lanlan"] == "K"  # 归属只给本次选定的角色卡
        assert "猫猫挥手" in call["parts"][0]["text"]

    def test_rejected_push_does_not_advance_clock(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 10.0, "K")]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        host.push.reject_reason = "payload_too_large"
        rejected = run_async(plugin._awareness.maybe_run(settings=_settings(), now=50.0))
        assert rejected["status"] == "push_rejected"
        retried = run_async(plugin._awareness.maybe_run(settings=_settings(), now=51.0))
        assert retried["status"] == "injected"

    def test_per_lanlan_clocks_are_independent(self, tmp_path, run_async):
        bus = FakeBus(
            [
                conversation_record("c1", 10.0, "K"),
                conversation_record("c2", 20.0, "M"),
            ]
        )
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=bus,
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        settings = _settings(interval=1000.0)
        first = run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))
        assert first["status"] == "injected" and first["target"] == "M"
        # 目标漂到另一张卡：新卡没有时钟，注；不许拿"等节奏"敷衍新出现的对话对象。
        bus.conversations.records = [conversation_record("c1", 999.0, "K")]
        second = run_async(plugin._awareness.maybe_run(settings=settings, now=60.0))
        assert second["status"] == "injected" and second["target"] == "K"

    def test_bus_failure_degrades_to_no_target(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([], error=True),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=50.0))
        assert result["status"] == "no_target"
        assert host.push.calls == []

    def test_maybe_run_never_leaks_exceptions(self, tmp_path, run_async, monkeypatch):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 10.0, "K")]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])

        async def explode(*args: Any, **kwargs: Any):
            raise RuntimeError("host went sideways")

        monkeypatch.setattr(plugin._awareness, "_run", explode)
        result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=50.0))
        assert result["status"] == "failed"


class TestInjectNow:
    def test_bypasses_interval_but_not_switches(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 10.0, "K")]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        settings = _settings(interval=100000.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "injected"
        # 间隔远未到期：自动拍 waiting，手动拍照样注（绕的是节奏，不是开关）。
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "waiting"
        forced = run_async(plugin._awareness.inject_now(settings=settings, lanlan="K", now=52.0))
        assert forced["status"] == "injected"
        blocked = run_async(
            plugin._awareness.inject_now(settings=_settings(enabled=False), lanlan="K", now=53.0)
        )
        assert blocked["status"] == "disabled"

    def test_manual_hint_targets_without_bus_read(self, tmp_path, run_async):
        # 面板入口带着 _ctx 的角色名来：不必查总线也能注（当前正在看的那张卡）。
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        result = run_async(plugin._awareness.inject_now(settings=_settings(), lanlan="K", now=50.0))
        assert result["status"] == "injected"
        assert host.push.calls[0]["target_lanlan"] == "K"


class TestSnapshot:
    def test_snapshot_reports_last_run_and_wait(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 10.0, "K")]),
        )
        plugin, host = build_plugin(host)
        before = plugin._awareness.snapshot(interval_sec=100.0, now=50.0)
        assert before["last_inject_at"] is None
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        run_async(plugin._awareness.maybe_run(settings=_settings(interval=100.0), now=50.0))
        after = plugin._awareness.snapshot(interval_sec=100.0, now=60.0)
        assert after["status"] == "injected"
        assert after["target"] == "K"
        assert after["last_inject_at"] == 50.0
        assert after["min_next_wait_sec"] == 90.0
