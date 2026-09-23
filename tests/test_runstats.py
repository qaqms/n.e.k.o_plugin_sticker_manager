# pyright: reportMissingImports=false
"""v0.17.1「观测轮」的门：三处读数必须真的在动，且不许泄露内容。

来由是主人那句"还是很少发表情包"。我去查的时候发现日志答不了这个问题——
它只有成功那一支有痕，于是**"她没调工具"与"她调了但被闸拦下"在日志里长得一模一样**，
而"目录到底挂上去了没有"只能靠人现场手读宿主的 `GET /api/tools`。
本轮零行为变更，只补读数；这个文件钉的就是读数本身的诚实性。

反向可验性（陷阱 5 的教训："只会变绿的门禁比没有门禁更危险"）：
每条门都配了一个"已知应该红"的形状——哨兵原文进日志、只是查目录却被算成发出、
描述未变却记了一笔 applied——这些只要有人写错，门就当众变红。
"""

from __future__ import annotations

from typing import Any

from conftest import PNG_BYTES, FakeBus, FakeConfig, FakeHostContext, build_plugin, user_message_record
from sticker_manager.core.configuration import (
    AwarenessSettings,
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)
from sticker_manager.core.tool_surface import build_send_tool_description
from sticker_manager.services.runstats import RunStats, gave_shape


class RecLogger:
    """录音假 logger：形状对齐真 logger（带 kwargs），只用来断言留痕。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def _emit(self, message: str = "", **_kwargs: Any) -> None:
        self.lines.append(str(message))

    info = _emit
    warning = _emit
    error = _emit
    debug = _emit

    def text(self) -> str:
        return "\n".join(self.lines)


def _plugin(tmp_path, *, enabled: bool = True):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": enabled}}),
    )
    plugin, host = build_plugin(host)
    plugin._settings = StickerManagerSettings(
        enabled=enabled,
        send=SendSettings(),
        storage=StorageSettings(),
        awareness=AwarenessSettings(),
    )
    plugin.logger = RecLogger()
    plugin._library.load(force=True)
    plugin._library.create_group("困与睡", "深夜犯困、撑着不想起时用", "")
    plugin._library.add(data=PNG_BYTES + b"|one", desc="哈欠", tags=[], group="困与睡")
    return plugin, host


class TestGaveShape:
    def test_nothing_given_is_none(self):
        assert gave_shape(sticker_id="", query="   ", group="", force=False) == "none"

    def test_only_real_parameters_count(self):
        assert gave_shape(sticker_id="a1", query="", group="", force=False) == "sticker_id"
        assert gave_shape(sticker_id="", query="困", group="困与睡", force=True) == "query+group+force"


class TestRunStatsCounters:
    def test_fresh_instance_is_all_zero(self):
        # "本次运行"的口径就靠这条钉住：新实例必须归零，不许长得像历史累计。
        snap = RunStats().snapshot(turns=0)
        assert snap["tool_calls"] == 0 and snap["sent"] == 0 and snap["refused"] == 0

    def test_list_lookup_counts_as_neither_sent_nor_blocked(self):
        # 反向样本：她只是查了目录，既没发出也没被拦——算进任何一边都是假账。
        stats = RunStats()
        stats.note_tool_call(name="sticker_list", gave="none", result={"ok": True, "count": 12})
        snap = stats.snapshot(turns=3)
        assert snap["tool_calls"] == 1 and snap["sent"] == 0 and snap["refused"] == 0

    def test_refusal_and_success_land_in_different_buckets(self):
        stats = RunStats()
        stats.note_tool_call(name="sticker_send", gave="group", result={"ok": False, "reason": "recent_repeat"})
        stats.note_tool_call(name="sticker_send", gave="group", result={"ok": True, "sent": "ab12"})
        snap = stats.snapshot(turns=5)
        assert (snap["tool_calls"], snap["refused"], snap["sent"]) == (2, 1, 1)
        assert snap["last_reason"] == "recent_repeat"


class TestToolCallTrace:
    def test_blocked_call_leaves_its_reason_in_the_log(self, tmp_path, run_async):
        # 本轮的要害：以前这种调用在日志里完全不留痕，"0 次发出"分辨不出病因。
        plugin, _host = _plugin(tmp_path)
        result = run_async(plugin.tool_sticker_send())
        assert result.get("ok") is False
        logged = plugin.logger.text()
        assert "tool call: name=sticker_send" in logged
        assert "outcome=refused reason=" in logged
        assert plugin._runstats.refused == 1 and plugin._runstats.sent == 0

    def test_query_text_never_reaches_the_log(self, tmp_path, run_async):
        # 隐私哨兵：她的检索词只许落长度。哨兵串出现在任何一行日志里 = 这条门红。
        plugin, _host = _plugin(tmp_path)
        sentinel = "哨兵原话绝不该进日志"
        run_async(plugin.tool_sticker_send(query=sentinel))
        assert sentinel not in plugin.logger.text()
        assert f"q_len={len(sentinel)}" in plugin.logger.text()

    def test_successful_call_is_counted_once_and_logged_once(self, tmp_path, run_async):
        plugin, _host = _plugin(tmp_path)
        result = run_async(plugin.tool_sticker_send(group="困与睡"))
        assert result.get("ok") is True, result
        assert plugin._runstats.sent == 1
        assert plugin.logger.text().count("tool call: name=sticker_send") == 1
        assert "outcome=sent" in plugin.logger.text()

    def test_panel_send_is_not_mixed_into_her_numbers(self, tmp_path, run_async):
        # 主人在面板上点"发到聊天"不算"她自己想发"——两把尺混了，读数就失去意义。
        plugin, _host = _plugin(tmp_path)
        sticker = plugin._library.all()[0]
        run_async(plugin.send_entry(id=sticker.id))
        assert plugin._runstats.tool_calls == 0
        assert "tool call:" not in plugin.logger.text()


class TestSurfaceAppliedTrace:
    def _attach(self, plugin, registry) -> None:
        for name in ("list_llm_tools", "unregister_llm_tool", "register_llm_tool"):
            setattr(plugin, name, getattr(registry, name))

    def test_real_change_logs_the_shape(self, tmp_path):
        plugin, _host = _plugin(tmp_path)
        registry = _Registry({"sticker_send": _meta("旧描述")})
        self._attach(plugin, registry)
        assert plugin._apply_send_tool_surface() is True
        line = [x for x in plugin.logger.lines if "surface applied" in x]
        assert line and "chars=" in line[0] and "categories=1" in line[0]
        assert plugin._runstats.surface_categories == 1
        assert plugin._runstats.surface_chars > 100

    def test_noop_change_logs_nothing(self, tmp_path):
        # 反向样本：没换却记了一笔 applied，读数就成了噪声——而"重挂到底生没生效"
        # 正是本轮要看的那件事。
        plugin, _host = _plugin(tmp_path)
        same = build_send_tool_description(plugin._send_tool_catalog(), plugin._settings.send.eagerness)
        registry = _Registry({"sticker_send": _meta(same)})
        self._attach(plugin, registry)
        assert plugin._apply_send_tool_surface() is True
        assert registry.calls == []
        assert "surface applied" not in plugin.logger.text()


class TestPanelReadings:
    def test_dashboard_carries_the_run_block(self, tmp_path, run_async):
        plugin, _host = _plugin(tmp_path)
        run_async(plugin.tool_sticker_list())
        payload = run_async(plugin.dashboard_context())
        run = payload["run"]
        assert run["tool_calls"] == 1
        for key in ("turns", "sent", "refused", "last_call", "surface_categories"):
            assert key in run, f"面板少了 {key} 这一格读数"

    def test_turns_ruler_is_cumulative_not_reset_by_injection(self, tmp_path, run_async):
        """`turns_seen`（分母）与 `turns_since_inject`（节奏计数）是两把尺。

        反向样本：把两者合成一把，注入一成功分母就归零，"她 40 轮里只调了 1 次"
        这种数就再也算不出来。
        """
        host = FakeHostContext(
            data_root=tmp_path,
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            bus=FakeBus([], memory_records=[user_message_record(100.0, "在吗", "K")]),
        )
        plugin, _host = build_plugin(host)
        plugin._library.load(force=True)
        plugin._library.add(data=PNG_BYTES + b"|t", desc="哈欠", tags=[])
        watcher = plugin._awareness._turns
        assert run_async(watcher.poll()) is not None
        assert watcher.turns_seen == 1
        assert run_async(watcher.poll()) is None  # 同一条用户轮不重复数
        assert watcher.turns_seen == 1
        watcher.reset_count("K")
        assert watcher.turns_since("K") == 0
        assert watcher.turns_seen == 1, "分母不许被注入清零"
        assert watcher.snapshot()["turns_seen"] == 1


# --- 假注册面（与 test_eagerness_tool 同一形状，这里只用到两处）----------------


class _Registry:
    def __init__(self, tools: dict):
        self.tools = dict(tools)
        self.calls: list[tuple[str, str]] = []

    def list_llm_tools(self):
        return [dict(meta) for meta in self.tools.values()]

    def unregister_llm_tool(self, name: str):
        self.calls.append(("unregister", name))
        return self.tools.pop(name, None) is not None

    def register_llm_tool(self, **kwargs):
        self.calls.append(("register", kwargs["description"]))
        self.tools[kwargs["name"]] = {
            "name": kwargs["name"],
            "description": kwargs["description"],
            "parameters": kwargs["parameters"],
            "timeout_seconds": kwargs["timeout"],
            "role": kwargs["role"],
        }
        return True


def _meta(description: str) -> dict:
    return {
        "name": "sticker_send",
        "description": description,
        "parameters": {"type": "object"},
        "timeout_seconds": 20.0,
        "role": None,
    }
