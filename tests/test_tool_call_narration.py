"""Prompt boundaries, not a guarantee that the model will obey them."""

from __future__ import annotations

import pytest

from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.awareness import build_awareness_text
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import EAGERNESS_LEVELS, StickerManagerSettings
from sticker_manager.core.eagerness import NEXT_STEP_NOTE
from sticker_manager.core.tool_surface import build_send_tool_description
from sticker_manager.services.sender import SendResult


def _assert_call_boundary(text):
    assert "实际调用工具 sticker_send，group 填分类名" in text
    assert "正文和配文不写工具名、参数或选图过程" in text
    assert "sticker_send(" not in text


@pytest.mark.parametrize("tier", EAGERNESS_LEVELS)
@pytest.mark.parametrize("catalog", ["", "・smile（1 张） — happy reaction"])
def test_standing_tool_surface_separates_actual_calls_from_narration(tier, catalog):
    _assert_call_boundary(build_send_tool_description(catalog, tier))


@pytest.mark.parametrize("tier", EAGERNESS_LEVELS)
@pytest.mark.parametrize("event", [False, True])
def test_hidden_reminders_keep_the_boundary_and_existing_transport_budget(tier, event):
    stickers = [Sticker(id=f"s{i}", file=f"{i}.png", desc="smile") for i in range(190)]
    text = build_awareness_text(stickers, eagerness=tier, event=event)
    _assert_call_boundary(text)
    assert "配图时实际调用" in text and "不贴切就纯文字" in text
    if tier == "eager":
        assert len(text) < 220


def test_legacy_send_schema_does_not_teach_a_fictional_call(tmp_path):
    plugin, _host = build_plugin(FakeHostContext(data_root=tmp_path))
    (schema,) = [
        item["kwargs"]["input_schema"]
        for item in plugin.send_entry.__neko_stub_meta__
        if "input_schema" in item["kwargs"]
    ]
    _assert_call_boundary(schema["properties"]["text"]["description"])


@pytest.mark.parametrize("handler_name", ["tool_sticker_send", "send_entry"])
@pytest.mark.parametrize("detail", [
    "immediate", "legacy_queue", "host_reply_tail", "host_reply_tail_uncertain",
])
def test_actual_send_receipts_preserve_boundary_and_delivery_state(
    tmp_path, monkeypatch, run_async, handler_name, detail,
):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(enabled=True)
    plugin._library.load(force=True)
    assert plugin._library.create_group("smile", "happy reaction", "")[1] == ""
    sticker, error = plugin._library.add(data=PNG_BYTES, desc="smile", tags=[], group="smile")
    assert not error

    if detail != "immediate":
        async def queue(selected, **kwargs):
            assert selected.id == sticker.id and kwargs["text"] == ""
            return SendResult(
                ok=True, sticker_id=selected.id, desc=selected.desc, queued=True,
                detail="" if detail == "legacy_queue" else detail,
            )

        monkeypatch.setattr(plugin._sender, "send", queue)

    result = run_async(getattr(plugin, handler_name)(group="smile", _ctx={"lanlan_name": "K"}))
    if handler_name == "send_entry":
        assert result.is_ok()
        result = result.value
    assert result["ok"] is True
    _assert_call_boundary(result["note"])
    if detail == "immediate":
        assert result["sent"] == sticker.id and result["note"] == NEXT_STEP_NOTE
        assert len(host.push.calls) == 1 and plugin._runstats.sent == 1
    else:
        assert result["queued"] == sticker.id and result["sent"] == ""
        assert "表情包已提交到聊天" not in result["note"]
        assert not host.push.calls and plugin._runstats.sent == 0
    if detail == "host_reply_tail_uncertain":
        assert "暂未确认" in result["note"] and "已登记" not in result["note"]
