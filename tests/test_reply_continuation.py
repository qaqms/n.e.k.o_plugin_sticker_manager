"""Tool receipts guide one continuing reply; they never replay ordinary text."""

import pytest
from sticker_manager.core.awareness import build_awareness_text
from sticker_manager.core.eagerness import INTERNAL_SELECTION_NOTE, REPLY_CONTINUATION_NOTE, REPLY_PACING_NOTE
from sticker_manager.core.tool_surface import build_send_tool_description

from .test_host_reply_tail import CONTEXT, setup


@pytest.mark.parametrize("tier", ["reserved", "natural", "eager"])
def test_text_first_is_optional_and_does_not_restart_at_each_tool(tmp_path, tier):
    plugin, _, _, sticker = setup(tmp_path)
    for text in (
        build_send_tool_description("wave", tier),
        next(
            row["kwargs"]["description"]
            for row in plugin.tool_sticker_list.__neko_stub_meta__
            if "description" in row["kwargs"]
        ),
    ):
        assert REPLY_PACING_NOTE in text
        assert "本轮开始时可先自然接话" in text
        assert "查询和发送之间不用再次接话" in text
        assert "先自然接话，再查候选" not in text
    reminder = build_awareness_text([sticker], eagerness=tier)
    assert "本轮可先接话" in reminder and "已说完就结束" in reminder


@pytest.mark.parametrize("state", ["registered", "uncertain", "submitted", "failed", "cancelled"])
def test_tail_receipts_allow_finished_text_to_end_without_new_speech(tmp_path, run_async, state):
    plugin, host, client, sticker = setup(tmp_path)
    client.receipt = {
        "status": state, "accepted": state in {"registered", "submitted"},
        **({"reason": "context_expired"} if state in {"failed", "cancelled"} else {}),
    }
    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"host_reply": CONTEXT}))
    assert result["hint"].startswith(INTERNAL_SELECTION_NOTE)
    assert REPLY_CONTINUATION_NOTE in result["hint"]
    assert "继续正常回复" not in result.get("note", "")
    assert not host.push.calls
    assert len(client.calls) == 1 and client.calls[0][0] == "register"
    assert all(part["type"] == "image" for part in client.calls[0][3])
    if state in {"registered", "uncertain"}:
        assert result["queued"] == sticker.id and result["sent"] == ""
        assert "若已说完就结束" in result["note"]
        assert ("暂未确认" in result["note"]) is (state == "uncertain")
    elif state == "submitted":
        assert result["sent"] == sticker.id and "若已说完就结束" in result["note"]
    else:
        assert not result["ok"] and result["reason"] == "context_expired"
