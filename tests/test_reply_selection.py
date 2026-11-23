"""Retrieval, compact requests and strict choice validation for completed replies."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from sticker_manager.core.attachment_selection import (
    parse_reply_selection,
    selection_candidates,
    selection_payload,
)
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.pack import parse_manifest_groups
from sticker_manager.services.bundled_pack import open_pack


@pytest.mark.parametrize(("reply", "expected"), [
    ("这件事我记下来了，下次一定不会忘记。", {"111", "112"}),
    ("晚安啦，我也准备早点睡了。", {"144", "145", "146", "147"}),
    ("我陪着你，慢慢来，不用一个人硬撑。", {"102", "117"}),
    ("我没听懂，脑子被你绕晕了。", {"047", "052", "053", "057"}),
    ("谢谢你愿意告诉我，收到你的心意啦。", {"004", "102"}),
    ("我才没有生气呢，就是有一点点气鼓鼓！", {"007"}),
    ("好期待明天见到你呀。", {"150", "151"}),
    ("我不开心，今天真的很难过。", {"037", "038"}),
    ("我帮你加油，你一定可以的！", {"027", "092"}),
    ("这太好笑了，哈哈哈哈！", {"089"}),
])
def test_bundled_sentence_cases_recall_a_relevant_candidate(reply, expected):
    root = Path(__file__).resolve().parents[1]
    with open_pack(root / "official/official_pack.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
    pool = [Sticker.from_dict({**row, "id": row["file"].split("_", 1)[0]}) for row in manifest["stickers"]]
    rows = selection_candidates(pool, reply, set(), parse_manifest_groups(manifest))[:16]
    assert expected & {row["id"] for row in rows}


def test_full_sentence_retrieval_recalls_an_attitude_beyond_fixed_common_moods():
    pool = [
        Sticker("a", "a.png", "", caption="自己开心地笑。", group="开心"),
        Sticker("b", "b.png", "", caption="自己害羞地低头。", group="害羞"),
        Sticker("c", "c.png", "", caption="认真记录对方说的话，记下来避免忘记。", tags=["记录", "记下来"], group="记录"),
    ]
    rows = selection_candidates(pool, "这件事我记下来了，下次一定不会忘记。", set(), {})
    assert rows[0]["id"] == "c"


def test_sentence_retrieval_covers_reply_ending_and_spaced_languages():
    pool = [
        Sticker("rest", "r.png", "", caption="自己犯困，准备休息，晚安。"),
        Sticker("smile", "s.png", "", caption="自己开心地笑。"),
        Sticker("thanks", "t.png", "", caption="Thank you for your help.", tags=["grateful", "thanks"]),
    ]
    assert selection_candidates(pool, "这是前面较长的聊天内容。" * 35 + "晚安，早点休息吧。", set(), {})[0]["id"] == "rest"
    assert selection_candidates(pool, "I'm grateful for your help today.", set(), {})[0]["id"] == "thanks"


def test_every_reply_keeps_recent_relevant_picture_instead_of_forcing_an_unrelated_one():
    pool = [
        Sticker("rest", "r.png", "", caption="自己犯困，准备休息，晚安。"),
        Sticker("weapon", "w.png", "", caption="举刀威胁对方，强烈警告。"),
    ]
    rows = selection_candidates(pool, "晚安，早点休息吧。", {"rest"}, {}, allow_recent=True)
    assert rows[0]["id"] == "rest" and rows[0]["recent"] is True
    assert [row["id"] for row in selection_candidates(pool, "晚安", {"rest"}, {})] == ["weapon"]


def test_retrieval_observes_library_annotation_changes_and_disabled_images():
    picture = Sticker("one", "1.png", "", caption="表示认真记录。")
    other = Sticker("two", "2.png", "", caption="开心大笑。")
    assert selection_candidates([picture, other], "认真记录", set(), {})[0]["id"] == "one"
    picture = replace(picture, caption="自己犯困，晚安。")
    other = replace(other, caption="认真记录下来了。")
    assert selection_candidates([picture, other], "认真记录", set(), {})[0]["id"] == "two"
    other = replace(other, disabled=True)
    assert [row["id"] for row in selection_candidates([picture, other], "认真记录", set(), {})] == ["one"]


def test_candidate_diversity_is_not_limited_to_a_fixed_emotion_list():
    pool = [
        Sticker(str(index), f"{index}.png", "", caption="测试分类的表情。", group=f"group-{index}")
        for index in range(16)
    ]
    assert len({row["group"] for row in selection_candidates(pool, "无字词重合", set(), {})[:16]}) == 16


def test_compact_payload_preserves_full_restrictions_and_does_not_repeat_current_reply():
    caption = "完整用途说明，不是嘲笑对方。" * 20
    rows = [{"id": "private-id", "caption": caption, "description": caption, "tags": [],
             "visible_text": "图内原文", "group": "g", "group_description": "分类说明", "recent": True}]
    history = [
        {"role": "user_message", "text": "以前的要求也要保留，不要用嘲讽图。"},
        {"role": "assistant_message", "text": "我记下来了。"},
    ]
    data = selection_payload("我记下来了。", history, rows)
    assert data["context"] == history[:1]
    assert data["candidates"][0]["caption"] == caption
    assert data["candidates"][0]["visible_text"] == "图内原文"
    assert data["candidates"][0]["recent"] is True
    assert "id" not in data["candidates"][0] and "description" not in data["candidates"][0]
    assert data["groups"] == {"g": "分类说明"}
    assert json.dumps(data, ensure_ascii=False).count("我记下来了。") == 1
    assert len(history) == 2 and rows[0]["id"] == "private-id"


@pytest.mark.parametrize("content", [
    '{"choice":0}', '{"choice":3}', '{"choice":true}', '{"choice":1.0}',
    '{"choice":"1"}', '{"choice":null}', '{"choice":1,"reason":"selected"}',
    '{"choice":"unknown"}', '```json\n{"choice":1}\n```', "[]",
])
def test_compact_choices_cannot_select_outside_the_current_candidates(content):
    assert parse_reply_selection(content, [{"id": "a"}, {"id": "b"}]) == ("", "invalid_result")


@pytest.mark.parametrize(("content", "expected"), [
    ('{"choice":2}', ("b", "selected")),
    ('{"choice":"no_match"}', ("", "no_match")),
    ('{"choice":"text_only"}', ("", "text_only")),
    ('{"choice":"serious_task"}', ("", "serious_task")),
    ('{"decision":"send","sticker_id":"a","reason":"selected"}', ("a", "selected")),
])
def test_compact_choices_resolve_exact_candidate_or_known_skip(content, expected):
    assert parse_reply_selection(content, [{"id": "a"}, {"id": "b"}]) == expected
