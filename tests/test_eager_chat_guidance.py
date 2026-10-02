# pyright: reportMissingImports=false
"""Eager-only prompt changes preserve the delivered 0.20.14 runtime contracts."""

from __future__ import annotations

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin, user_message_record
from sticker_manager.core.awareness import build_awareness_text
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import StickerManagerSettings
from sticker_manager.core.eagerness import (
    DECISION_CHECKPOINT,
    NEXT_STEP_NOTE,
    TOOL_NOTE,
    TRIGGER_CRITERIA,
    WILL,
    decision_checkpoint,
    injection_guidance,
    injection_pointer,
    tool_criteria,
)
from sticker_manager.core.tool_surface import build_send_tool_description
from sticker_manager.services.awareness import Awareness

_UNCHANGED_014 = {
    "reserved": {
        "will": (
            "发之前先想清楚这张图在回复什么：分清你是在安慰对方还是在说自己，"
            "拿不准、不贴切就不发；一条回复配一张就够，宁缺毋滥。"
            "多数时候纯文字就够了，图是点缀不是必需品——没有正合适的就别发。"
        ),
        "checkpoint": (
            "开口之前先做一次判断：这句配上图会不会比纯文字更准？判断不用配就直接用文字回。"
        ),
        "criteria": (
            "什么时候该想到它：你这句要是换成一张图会更准、更俏皮，才配；"
            "正经回答、报信息、办事的时候纯文字。"
        ),
        "note": " 矜持档：只在非常贴切时配一张，多数回复纯文字就很好。",
    },
    "natural": {
        "will": (
            "发之前先想清楚这张图在回复什么：分清你是在安慰对方还是在说自己，"
            "拿不准、不贴切就不发；一条回复配一张就够，宁缺毋滥。"
        ),
        "checkpoint": (
            "开口之前先做一次判断：这句配上图会不会比纯文字更准、更俏皮？"
            "判断该配就调用本工具，别因为「文字已经说清楚了」就跳过这一步；"
            "判断不该配就直接用文字回，跳过是允许的。"
        ),
        "criteria": (
            "什么时候该想到它：摸摸、贴贴、玩笑、轻松接话，或你想表达开心、无语、心疼、撒娇、吐槽时值得配一张；"
            "纯信息问答、算东西、报时间的时候纯文字。"
        ),
        "note": " 贴切就配一张，不贴切就别硬找。",
    },
}
_SHARED_POINTER_014 = (
    "回复有情绪或轻松互动时，按你想表达的态度选分类，调用 sticker_send(group=分类名)。"
    "正常文字回复照常说，text 留空即可，图片独立发送；没有合适的就纯文字。"
)
_NEXT_STEP_014 = (
    "表情包已提交到聊天，继续正常文字回复，不复述额外配文。"
    "后续互动想配图时仍可调用 sticker_send(group=分类名)，text 留空即可；没合适的就纯文字。"
)


def _stickers(count=1):
    return [Sticker(id=f"s{i}", file=f"{i}.png", desc="smile") for i in range(count)]


@pytest.mark.parametrize("tier", ["reserved", "natural"])
def test_non_eager_text_is_byte_for_byte_014_baseline(tier):
    expected = _UNCHANGED_014[tier]
    assert WILL[tier] == injection_guidance(tier) == expected["will"]
    assert DECISION_CHECKPOINT[tier] == decision_checkpoint(tier) == expected["checkpoint"]
    assert TRIGGER_CRITERIA[tier] == tool_criteria(tier) == expected["criteria"]
    assert TOOL_NOTE[tier] == expected["note"]


@pytest.mark.parametrize("tier", ["reserved", "natural", "eager"])
def test_shared_pointer_and_next_step_keep_014_baseline(tier):
    assert injection_pointer(tier) == _SHARED_POINTER_014
    assert NEXT_STEP_NOTE == _NEXT_STEP_014


@pytest.mark.parametrize("junk", ["wild", None, ["eager"]])
def test_unknown_tiers_keep_natural_text_on_actual_surfaces(junk):
    expected = _UNCHANGED_014["natural"]
    assert injection_guidance(junk) == expected["will"]
    assert decision_checkpoint(junk) == expected["checkpoint"]
    assert tool_criteria(junk) == expected["criteria"]
    catalog = "・smile（1 张） — happy reaction"
    assert build_send_tool_description(catalog, junk) == build_send_tool_description(catalog, "natural")
    assert build_awareness_text(_stickers(), eagerness=junk) == build_awareness_text(
        _stickers(), eagerness="natural",
    )


def test_eager_tool_surface_prioritizes_fitting_image_for_ordinary_chat():
    description = build_send_tool_description("・smile（1 张） — happy reaction", "eager")
    assert description.startswith("每轮开口前先做这个判断：按自己要表达的态度找分类。")
    for scene in ("轻松接话", "开心庆祝", "撒娇贴贴", "关心安慰", "玩笑吐槽", "犯困", "道晚安"):
        assert scene in description
    assert "有贴切分类就优先调用 sticker_send 配一张" in description
    assert "不用等用户点名要图" in description
    assert "也别因文字已经说清楚就省掉" in description
    assert "按自己回复要表达的态度选分类" in description
    assert "分清安慰对方和说自己" in description
    assert "无贴切分类、信息问答或正经办事就纯文字" in description
    assert "单回复最多一张" in description
    assert "正常文字回复照常说，text 留空即可，图片独立发送" in description
    assert "日常只需 sticker_send(group=分类名)" in description


def test_eager_guidance_has_no_turn_quota_or_indiscriminate_fill():
    for text in (WILL["eager"], DECISION_CHECKPOINT["eager"], TRIGGER_CRITERIA["eager"], TOOL_NOTE["eager"]):
        assert "贴切" in text and "纯文字" in text and "单回复最多一张" in text
        for banned in ("连续", "连着", "随机", "每三轮", "每3轮", "必须配", "必须发", "每轮都发", "概率", "配额"):
            assert banned not in text


@pytest.mark.parametrize("event", [False, True])
def test_eager_reminder_budget_covers_190_stickers_and_both_triggers(event):
    text = build_awareness_text(_stickers(190), eagerness="eager", event=event)
    assert "190 张表情包" in text
    assert "贴切就优先配一张，不等要图，文字说清也别省掉" in text
    assert "分清安慰与自述，不贴切或正经办事纯文字，单回复最多一张" in text
    assert _SHARED_POINTER_014 in text
    assert len(text) < 220
    assert text.startswith("（表情包点名）") is event


@pytest.mark.parametrize("trigger", ["count", "signal"])
def test_actual_awareness_push_gets_eager_text_without_changing_cadence_or_transport(
    tmp_path, run_async, trigger,
):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"enabled": True, "send": {"eagerness": "eager"}}}
    )
    plugin._library.load(force=True)
    assert plugin._library.add(data=PNG_BYTES, desc="smile", tags=[])[1] == ""
    awareness = Awareness(plugin, plugin._library)
    assert settings.awareness.inject_mode == "interval_n"
    assert settings.awareness.inject_interval_n == 0
    assert settings.awareness.min_interval_sec == 60.0
    assert settings.awareness.event_gated is True
    assert Awareness.interval_n(settings) == 3

    texts = ["第1条", "第2条", "第3条"] if trigger == "count" else ["好开心"]
    for turn_n, content in enumerate(texts, start=1):
        host.bus.memory.records.append(user_message_record(float(turn_n), content, "K"))
        result = run_async(awareness.maybe_run(settings=settings, now=100.0 + turn_n))
        if turn_n < len(texts):
            assert result["status"] == "not_due"
            assert host.push.calls == []
    assert result["status"] == "injected" and result["trigger"] == trigger
    (call,) = host.push.calls
    assert call["visibility"] == []
    assert call["ai_behavior"] == "read"
    assert call["target_lanlan"] == "K"
    assert call["coalesce_key"] == "sticker_manager:awareness:K"
    assert call["description"] == "sticker_manager:awareness"
    assert call["parts"][0]["type"] == "text"
    text = call["parts"][0]["text"]
    assert "贴切就优先配一张，不等要图，文字说清也别省掉" in text
    assert "分清安慰与自述" in text and "单回复最多一张" in text
    assert _SHARED_POINTER_014 in text
    assert text.startswith("（表情包点名）") is (trigger == "signal")
    assert awareness.turns_seen == len(texts)
    assert awareness.trigger_counts == {trigger: 1}

    injected_at = awareness.last_inject_at
    host.bus.memory.records.append(user_message_record(10.0, "抱抱", "K"))
    blocked = run_async(awareness.maybe_run(settings=settings, now=injected_at + 59.0))
    assert blocked["status"] == "not_due" and len(host.push.calls) == 1
    host.bus.memory.records.append(user_message_record(11.0, "抱抱", "K"))
    due = run_async(awareness.maybe_run(settings=settings, now=injected_at + 60.0))
    assert due["status"] == "injected" and due["trigger"] == "signal"
    assert len(host.push.calls) == 2
