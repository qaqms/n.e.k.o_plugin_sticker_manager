"""轮次驱动注入（v0.16.0）：总线记录解析 + 水位计数 + 节奏闸。

钉四族：
1. **解析**（纯函数）：宿主原始形状 / SDK 多层包装 / 非用户消息 / 坏时间戳；
2. **水位与计数**（TurnWatcher）：同一轮只看见一次、每卡独立、计数只在显式 reset 后归零；
3. **降级判据**：总线读不通 ≠ 桶是空的——前者退回挂钟，后者是什么都不做；
4. **节奏闸**（core.injection_due_for_turn + Awareness 串联）：N 轮攒一次、地板挡住时
   不消耗计数、每轮都注档的实机口径。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import (
    PNG_BYTES,
    FakeBus,
    FakeConfig,
    FakeHostContext,
    build_plugin,
    conversation_record,
    user_message_record,
)
from sticker_manager.core.awareness import (  # pyright: ignore[reportMissingImports] — 包名由 conftest 在测试时注册；独立仓静态面不可解析
    injection_due_for_turn,
    normalize_mode,
)
from sticker_manager.core.configuration import (  # pyright: ignore[reportMissingImports] — 同上
    AwarenessSettings,
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)
from sticker_manager.services.turns import (  # pyright: ignore[reportMissingImports] — 同上
    latest_user_turn,
    user_turn_of,
)


class _Wrap:
    """SDK 包装层的三种现实形态：.payload / .raw / {"value": ...} 再套一层。"""

    def __init__(self, payload: Any, *, use_raw: bool = False, timestamp: float | None = None):
        if use_raw:
            self.raw = payload
        else:
            self.payload = payload
        if timestamp is not None:
            self.timestamp = timestamp


class TestRecordParsing:
    def test_host_shape_reads_through(self):
        turn = user_turn_of(user_message_record(1234.5, "在吗", "YUI"))
        assert turn is not None
        assert (turn.ts, turn.text, turn.lanlan) == (1234.5, "在吗", "YUI")
        assert turn.is_voice is False

    def test_voice_flag_survives(self):
        turn = user_turn_of(user_message_record(1.0, "喂", "K", is_voice=True))
        assert turn is not None and turn.is_voice is True

    def test_sdk_value_wrapper_is_unwrapped(self):
        # 同门实机症状：from_raw 把非 Mapping 包成 {"value": record}，type 被吞。
        inner = _Wrap({"type": "user_message", "content": "hi", "_ts": 7.0, "lanlan": "K"})
        turn = user_turn_of(_Wrap({"value": inner}))
        assert turn is not None
        assert (turn.ts, turn.text, turn.lanlan) == (7.0, "hi", "K")

    def test_raw_attribute_shape_is_unwrapped(self):
        turn = user_turn_of(_Wrap({"type": "user_message", "content": "x", "_ts": 9.0}, use_raw=True))
        assert turn is not None and turn.ts == 9.0

    def test_missing_ts_falls_back_to_record_timestamp(self):
        turn = user_turn_of(_Wrap({"type": "user_message", "content": "x"}, timestamp=42.0))
        assert turn is not None and turn.ts == 42.0

    @pytest.mark.parametrize(
        "record",
        [
            {"type": "assistant_reply", "content": "x", "_ts": 1.0},
            {"content": "x", "_ts": 1.0},
            "not-a-record",
            None,
            {"type": "user_message"},  # 没内容没时间戳也要能读成 0 秒的一轮
        ],
    )
    def test_non_user_records_are_ignored(self, record: Any):
        if record == {"type": "user_message"}:
            turn = user_turn_of(record)
            assert turn is not None and turn.ts == 0.0
        else:
            assert user_turn_of(record) is None

    def test_string_timestamp_does_not_cut_to_the_front(self):
        # 坏时间戳当 0 垫底（与 core._lenient_float 同纪律）：不许插队成"最新一轮"。
        turn = user_turn_of({"type": "user_message", "content": "x", "_ts": "1e400"})
        assert turn is not None and turn.ts == 0.0

    def test_latest_user_turn_scans_backwards_past_noise(self):
        records = [
            user_message_record(10.0, "早", "K"),
            {"type": "other", "_ts": 99.0},
            user_message_record(20.0, "晚", "K"),
        ]
        turn = latest_user_turn(records)
        assert turn is not None and turn.text == "晚"

    def test_latest_user_turn_of_nothing_is_none(self):
        assert latest_user_turn([]) is None
        assert latest_user_turn([{"type": "other"}]) is None


@pytest.fixture
def watcher():
    """造一个挂在假宿主上的轮次源，连同 bus 一起回（改 bus.memory.records 才有新轮）。"""

    def _make(records: list[dict[str, Any]] | None = None, *, error: bool = False) -> tuple[Any, Any]:
        bus = FakeBus([], memory_records=records, memory_error=error)
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            bus=bus,
        )
        plugin, _host = build_plugin(host)
        return plugin._awareness._turns, bus

    return _make


class TestTurnWatcher:
    def test_first_sight_is_a_new_turn(self, run_async: Any, watcher: Any):
        w, _bus = watcher([user_message_record(100.0, "在吗", "K")])
        turn = run_async(w.poll())
        assert turn is not None and turn.lanlan == "K" and turn.text == "在吗"
        assert w.available is True

    def test_same_turn_is_not_seen_twice(self, run_async: Any, watcher: Any):
        w, _bus = watcher([user_message_record(100.0, "在吗", "K")])
        assert run_async(w.poll()) is not None
        assert run_async(w.poll()) is None
        assert w.turns_since("K") == 1  # 计数不因为多问几拍而涨

    def test_watermark_advances_only_forward(self, run_async: Any, watcher: Any):
        w, bus = watcher([user_message_record(100.0, "第一句", "K")])
        assert run_async(w.poll()) is not None
        bus.memory.records.append(user_message_record(90.0, "更早的一条", "K"))
        assert run_async(w.poll()) is None

    def test_counts_are_per_card(self, run_async: Any, watcher: Any):
        w, bus = watcher([user_message_record(100.0, "叫谁呢", "K")])
        assert run_async(w.poll()) is not None
        bus.memory.records = [
            user_message_record(100.0, "叫谁呢", "K"),
            user_message_record(101.0, "叫我", "M"),
        ]
        assert run_async(w.poll()) is not None  # 最新一条换成了 M 的轮
        assert w.turns_since("M") == 1
        bus.memory.records.append(user_message_record(102.0, "又找我", "K"))
        assert run_async(w.poll()) is not None
        assert w.turns_since("K") == 2 and w.turns_since("M") == 1

    def test_reset_count_clears_only_that_card(self, run_async: Any, watcher: Any):
        w, _bus = watcher([user_message_record(100.0, "a", "K")])
        run_async(w.poll())
        w.reset_count("K")
        assert w.turns_since("K") == 0

    def test_empty_bucket_keeps_the_source_alive(self, run_async: Any, watcher: Any):
        # "桶是空的"（没人说话）与"总线读不通"（形状变了）是两回事：
        # 前者 available=True → 上层什么都不做；后者 available=False → 退回挂钟。
        w, _bus = watcher([])
        assert run_async(w.poll()) is None
        assert w.available is True

    def test_bus_error_marks_source_unavailable(self, run_async: Any, watcher: Any):
        w, _bus = watcher(error=True)
        assert run_async(w.poll()) is None
        assert w.available is False

    def test_turn_owner_falls_back_to_current_card(self, run_async: Any, watcher: Any):
        w, bus = watcher([user_message_record(100.0, "没署名")])
        w._plugin.ctx._current_lanlan = "NOW"
        turn = run_async(w.poll())
        assert turn is not None and turn.lanlan == "NOW"
        assert bus.memory.calls[-1]["bucket_id"] == "default"  # 读的就是宿主那个桶

    def test_unsigned_turn_without_current_card_is_dropped(self, run_async: Any, watcher: Any):
        # 归属不明的轮次不计数：钉在空名字上会让计数永远攒不满。
        w, _bus = watcher([user_message_record(100.0, "没署名")])
        assert run_async(w.poll()) is None
        assert w.available is True

    def test_snapshot_reports_driver_and_counts(self, run_async: Any, watcher: Any):
        w, _bus = watcher([user_message_record(100.0, "在吗", "K")])
        run_async(w.poll())
        snap = w.snapshot()
        assert snap["source"] == "bus"
        assert snap["turns_since_inject"] == {"K": 1}
        assert snap["latest_turn_ts"] == 100.0

    def test_poll_never_raises_on_broken_bus(self, run_async: Any):
        class _Boom:
            def get(self, **kwargs: Any) -> Any:
                raise RuntimeError("zmq went sideways")

        host = FakeHostContext(config=FakeConfig(data={"sticker_manager": {"enabled": True}}))
        host.bus = type("B", (), {"memory": _Boom(), "conversations": _Boom()})()
        plugin, _host = build_plugin(host)
        assert run_async(plugin._awareness._turns.poll()) is None


class TestInjectionDue:
    def test_interval_n_waits_for_n_turns(self):
        assert injection_due_for_turn("interval_n", turns_since_inject=2, interval_n=3, floor_remaining_sec=0.0) is False
        assert injection_due_for_turn("interval_n", turns_since_inject=3, interval_n=3, floor_remaining_sec=0.0) is True

    def test_every_mode_fires_on_any_new_turn(self):
        assert injection_due_for_turn("every_user_message", turns_since_inject=1, interval_n=99, floor_remaining_sec=0.0) is True

    def test_floor_beats_both_modes(self):
        for mode in ("every_user_message", "interval_n"):
            assert (
                injection_due_for_turn(mode, turns_since_inject=99, interval_n=1, floor_remaining_sec=1.0)
                is False
            )

    def test_interval_n_below_one_is_clamped_to_one(self):
        assert injection_due_for_turn("interval_n", turns_since_inject=1, interval_n=0, floor_remaining_sec=0.0) is True

    @pytest.mark.parametrize("mode", ["", "whenever", None, 7, "OFF"])
    def test_unlisted_mode_degrades_to_default(self, mode: Any):
        assert normalize_mode(mode) == "interval_n"


class TestTurnDrivenAwareness:
    """端到端：新的驱动源接上注入器之后，节奏到底长什么样。"""

    @staticmethod
    def _plugin(tmp_path: Any, records: list[dict[str, Any]]) -> tuple[Any, Any]:
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 1.0, "K")], memory_records=records),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="猫猫挥手", tags=[])
        return plugin, host

    def test_no_new_turn_injects_nothing(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "在吗", "K")])
        settings = _turns(mode="every_user_message", floor=0.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "injected"
        # 同一条用户轮还在桶里：不重复注。老挂钟会在下一个小时再来一次，这正是本轮拆掉的行为。
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "idle"
        assert len(host.push.calls) == 1

    def test_interval_n_fires_on_the_third_turn(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "一", "K")])
        memory = host.bus.memory
        settings = _turns(mode="interval_n", interval_n=3, floor=0.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "not_due"
        memory.records.append(user_message_record(101.0, "二", "K"))
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "not_due"
        assert host.push.calls == []
        memory.records.append(user_message_record(102.0, "三", "K"))
        due = run_async(plugin._awareness.maybe_run(settings=settings, now=52.0))
        assert due["status"] == "injected"
        assert host.push.calls[-1]["target_lanlan"] == "K"

    def test_floor_block_does_not_consume_the_count(self, tmp_path, run_async):
        # 地板只在"已经注过一次"之后生效（首轮没有历史可挡），所以先注一发再验：
        # 攒够 N 轮但地板没过时计数留着，下一轮立刻命中——否则"每 3 轮"会静默
        # 退化成"每 4、5 轮"（同门 whisper.py 的同一把尺）。
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "一", "K")])
        memory = host.bus.memory
        settings = _turns(mode="interval_n", interval_n=1, floor=1000.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "injected"
        memory.records.append(user_message_record(101.0, "二", "K"))
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "not_due"
        assert plugin._awareness._turns.turns_since("K") == 1  # 被地板挡住：配额不烧
        memory.records.append(user_message_record(102.0, "三", "K"))
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=9999.0))["status"] == "injected"

    def test_failed_push_keeps_the_count(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "一", "K")])
        settings = _turns(mode="interval_n", interval_n=1, floor=0.0)
        host.push.reject_reason = "backpressure"  # 一次性：消费掉自己就恢复正常
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "push_rejected"
        assert plugin._awareness._turns.turns_since("K") == 1  # 被拒不烧配额
        host.bus.memory.records.append(user_message_record(101.0, "二", "K"))
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "injected"
        assert plugin._awareness._turns.turns_since("K") == 0

    def test_injection_resolves_target_from_the_turn(self, tmp_path, run_async):
        # 轮次记录自己带归属角色：即使 conversations 桶在说谎，也注给它而不是别人。
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "在吗", "YUI")])
        run_async(plugin._awareness.maybe_run(settings=_turns(floor=0.0, interval_n=1), now=50.0))
        assert host.push.calls[-1]["target_lanlan"] == "YUI"

    def test_driver_shows_as_bus_once_the_source_works(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "在吗", "K")])
        run_async(plugin._awareness.maybe_run(settings=_turns(floor=0.0, interval_n=1), now=50.0))
        snap = plugin._awareness.snapshot(settings=_turns(floor=0.0, interval_n=1), now=51.0)
        assert snap["driver"] == "bus"
        assert snap["inject_mode"] == "interval_n" and snap["inject_interval_n"] == 1


def _turns(
    *,
    mode: str = "interval_n",
    interval_n: int = 3,
    floor: float = 60.0,
) -> StickerManagerSettings:
    return StickerManagerSettings(
        enabled=True,
        send=SendSettings(),
        storage=StorageSettings(),
        awareness=AwarenessSettings(
            enabled=True,
            inject_mode=mode,
            inject_interval_n=interval_n,
            min_interval_sec=floor,
        ),
    )


class TestConfigReadIn:
    """新键的读入面：垃圾必须收敛成合法值，不许让她的行为变野。"""

    @staticmethod
    def _awareness(**raw: Any) -> AwarenessSettings:
        # from_config 吃的是整份配置（顶层 `[sticker_manager]` 段），不是段本身。
        return StickerManagerSettings.from_config(
            {"sticker_manager": {"enabled": True, "awareness": raw}}
        ).awareness

    def test_defaults_are_turn_driven_with_a_house_proven_cadence(self):
        settings = self._awareness()
        assert settings.inject_mode == "interval_n"
        assert settings.inject_interval_n == 3
        assert settings.min_interval_sec == 60.0
        assert settings.interval_sec == 3600.0  # 降级时钟保留老默认

    def test_written_keys_are_honoured(self):
        settings = self._awareness(
            inject_mode="every_user_message", inject_interval_n=7, min_interval_sec=5.0
        )
        assert settings.inject_mode == "every_user_message"
        assert settings.inject_interval_n == 7
        assert settings.min_interval_sec == 5.0

    @pytest.mark.parametrize("mode", ["", "off", "ON_TRIGGER", 7, None])
    def test_unknown_mode_sinks_to_interval_n(self, mode: Any):
        assert self._awareness(inject_mode=mode).inject_mode == "interval_n"

    def test_out_of_range_numbers_are_clamped(self):
        settings = self._awareness(inject_interval_n=0, min_interval_sec=-30.0)
        assert settings.inject_interval_n == 1  # <1 按 1 收 = 每轮
        assert settings.min_interval_sec == 0.0  # 地板可以关掉
