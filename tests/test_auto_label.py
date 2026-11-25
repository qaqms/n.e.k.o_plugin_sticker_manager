"""自动识图（第 2 阶段）：后台队列能停能续、结果一张一存、主人优先。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sticker_manager.core.auto_label import build_prompt, parse_answer, plan_patch
from sticker_manager.core.catalog import Sticker
from sticker_manager.services.auto_label import (
    CONSECUTIVE_FAILURE_LIMIT,
    STATE_FILENAME,
    AutoLabeler,
    encode_image,
)
from sticker_manager.services.library import Library

GOOD = json.dumps({
    "name": "开心转圈", "caption": "开心得转圈圈，适合回应好消息",
    "visible_text": "耶", "tags": ["开心", "转圈", "庆祝", "可爱"], "group": "开心",
}, ensure_ascii=False)


class ModelError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.body = {"error": {"code": code}}


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else GOOD
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


def _setup(tmp_path, replies=(), *, count=3, models=True):
    lib = Library(tmp_path)
    assert lib.load().ok
    ids = []
    for i in range(count):
        sticker, error = lib.add(data=b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64, desc=f"IMG {i}", tags=[], now=100.0 + i)
        assert error == ""
        ids.append(sticker.id)
    client = FakeClient(replies)

    async def get_client():
        return client

    ctx = SimpleNamespace(models=SimpleNamespace(get_client=get_client)) if models else SimpleNamespace()
    labeler = AutoLabeler(SimpleNamespace(ctx=ctx), lib, encode=lambda path: ("data:image/png;base64,AA==", False))
    return lib, labeler, client, ids


def _run(labeler, ticks=1):
    out = None
    for _ in range(ticks):
        out = asyncio.run(labeler.tick())
    return out


def test_each_result_is_saved_immediately_and_survives_restart(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path)
    assert labeler.start_missing() == 3
    assert _run(labeler)["status"] == "done"
    # 新进程：只看盘
    lib2 = Library(tmp_path)
    assert lib2.load().ok
    first = lib2.get(ids[0])
    assert first.caption.startswith("开心") and first.label_state == "done"
    assert first.desc == "开心转圈"  # 文件名式短名被换掉
    assert set(first.auto_fields) >= {"caption", "tags", "visible_text", "desc"}
    assert lib2.label_ids("queued") == ids[1:]
    labeler2 = AutoLabeler(labeler.plugin, lib2, encode=labeler._encode)
    labeler2.recover()
    assert labeler2.snapshot()["run"]["done"] == 1
    _run(labeler2, 2)
    snap = labeler2.snapshot()
    assert snap["status"] == "finished" and snap["run"]["done"] == 3


def test_working_sticker_goes_back_to_queue_on_restart(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path)
    labeler.start_missing()
    lib.set_label_states([ids[0]], "working")
    lib2 = Library(tmp_path)
    assert lib2.load().ok
    assert AutoLabeler(labeler.plugin, lib2).recover() == 1
    assert lib2.label_ids("queued") == ids


def test_pause_stops_and_resume_continues(tmp_path):
    lib, labeler, client, _ = _setup(tmp_path)
    labeler.start_missing()
    _run(labeler)
    labeler.pause()
    assert _run(labeler, 3)["status"] == "paused"
    assert client.calls == 1
    assert labeler.snapshot()["status"] == "paused"
    # 暂停跨重启保留
    again = AutoLabeler(labeler.plugin, lib, encode=labeler._encode)
    assert again.snapshot()["reason"] == "user"
    again.resume()
    _run(again, 2)
    assert client.calls == 3 and again.snapshot()["status"] == "finished"


def test_clear_keeps_finished_results(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path)
    labeler.start_missing()
    _run(labeler)
    assert labeler.clear() == 2
    assert lib.get(ids[0]).label_state == "done"
    assert lib.get(ids[1]).label_state == "" and not lib.get(ids[1]).caption
    assert labeler.snapshot()["status"] == "finished"
    labeler.dismiss()
    assert labeler.snapshot()["status"] == "idle"


def test_owner_edit_during_labeling_wins(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path, count=1)
    labeler.start_missing()
    lib.update(ids[0], caption="主人写的梗义")
    _run(labeler)
    sticker = lib.get(ids[0])
    assert sticker.caption == "主人写的梗义"
    assert sticker.desc == "IMG 0"  # 措辞绑成一块让位
    assert "caption" not in sticker.auto_fields and sticker.tags  # 标签照填


def test_owner_edit_after_labeling_takes_ownership(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path, count=1)
    labeler.start_missing()
    _run(labeler)
    lib.update(ids[0], tags=["我的"])
    sticker = lib.get(ids[0])
    assert "tags" not in sticker.auto_fields and "tags" in sticker.owner_edited


def test_deleted_while_queued_is_skipped(tmp_path):
    lib, labeler, client, ids = _setup(tmp_path, count=2)
    labeler.start_missing()
    lib.remove(ids[0])
    _run(labeler, 2)
    assert client.calls == 1 and lib.get(ids[1]).label_state == "done"


def test_quota_pauses_and_puts_image_back(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path, [ModelError("agent_quota_exceeded")])
    labeler.start_missing()
    _run(labeler)
    snap = labeler.snapshot()
    assert snap["status"] == "paused" and snap["reason"] == "quota"
    assert lib.get(ids[0]).label_state == "queued" and lib.get(ids[0]).label_attempts == 0


def test_quota_and_cap_resume_next_day(tmp_path, monkeypatch):
    _, labeler, _, _ = _setup(tmp_path, [ModelError("agent_quota_exceeded")])
    labeler.start_missing()
    _run(labeler)
    state = json.loads((tmp_path / STATE_FILENAME).read_text(encoding="utf-8"))
    state["day"] = "2000-01-01"
    (tmp_path / STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")
    fresh = AutoLabeler(labeler.plugin, labeler.library, encode=labeler._encode)
    assert fresh.snapshot()["status"] == "running"


def test_daily_cap_pauses(tmp_path):
    _, labeler, client, _ = _setup(tmp_path)
    labeler.configure(daily_cap=1)
    labeler.start_missing()
    _run(labeler, 3)
    snap = labeler.snapshot()
    assert client.calls == 1 and snap["reason"] == "daily_cap"
    labeler.configure(daily_cap=10)
    assert labeler.snapshot()["status"] == "running"


def test_unbound_model_pauses_without_spending_attempts(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path, [ModelError("model_usage_not_bound")])
    labeler.start_missing()
    _run(labeler)
    assert labeler.snapshot()["reason"] == "model"
    assert lib.get(ids[0]).label_attempts == 0


def test_bad_answers_fail_after_three_attempts(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path, ["不是 JSON"] * 3, count=1)
    labeler.start_missing()
    _run(labeler, 3)
    assert lib.get(ids[0]).label_state == "failed"
    snap = labeler.snapshot()
    assert snap["run"]["failed"] == 1 and snap["failed_total"] == 1
    assert labeler.retry_failed() == 1
    _run(labeler)
    assert lib.get(ids[0]).label_state == "done"


def test_consecutive_failures_pause(tmp_path):
    _, labeler, client, _ = _setup(tmp_path, [TimeoutError()] * 10, count=5)
    labeler.start_missing()
    _run(labeler, CONSECUTIVE_FAILURE_LIMIT + 2)
    assert client.calls == CONSECUTIVE_FAILURE_LIMIT
    assert labeler.snapshot()["reason"] == "failures"


def test_unreadable_file_fails_without_model_call(tmp_path):
    lib, labeler, client, ids = _setup(tmp_path, count=1)

    def broken(path):
        raise OSError("gone")

    labeler._encode = broken
    labeler.start_missing()
    _run(labeler)
    assert client.calls == 0 and lib.get(ids[0]).label_state == "failed"


def test_old_host_without_models_is_unsupported(tmp_path):
    _, labeler, _, _ = _setup(tmp_path, models=False)
    assert labeler.snapshot()["status"] == "unsupported"


def test_relabel_one_jumps_the_queue(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path)
    labeler.start_missing()
    _run(labeler, 3)
    labeler.start_missing()  # 全都有梗义了，不再排
    assert lib.label_ids("queued") == []
    labeler.enqueue(ids[:2])
    labeler.label_one(ids[2])
    out = _run(labeler)
    assert out["id"] == ids[2]


def test_after_import_respects_switch(tmp_path):
    lib, labeler, _, _ = _setup(tmp_path, count=0)
    labeler.configure(auto_after_import=False)
    lib.add(data=b"\x89PNG\r\n\x1a\n" + b"\x09" * 64, desc="", tags=[], now=500.0)
    assert labeler.after_import(400.0) == 0
    labeler.configure(auto_after_import=True)
    assert labeler.after_import(400.0) == 1


def test_parallel_ticks_do_not_double_process(tmp_path):
    _, labeler, client, _ = _setup(tmp_path, count=2)
    labeler.start_missing()

    async def both():
        return await asyncio.gather(labeler.tick(), labeler.tick())

    results = asyncio.run(both())
    assert sorted(r["status"] for r in results) == ["busy", "done"]
    assert client.calls == 1


# ---------------------------------------------------------------------------
# 纯判定尺
# ---------------------------------------------------------------------------


def _sticker(**kw) -> Sticker:
    return Sticker(id="a", file="a.png", desc=kw.pop("desc", ""), **kw)


def test_plan_patch_fills_only_empty_fields():
    answer = parse_answer(GOOD)
    patch = plan_patch(_sticker(desc="我起的名字", visible_text="原文"), answer, {"开心"})
    assert "desc" not in patch and "visible_text" not in patch
    assert patch["group"] == "开心" and patch["caption"]


def test_plan_patch_only_suggests_when_already_grouped():
    patch = plan_patch(_sticker(group="难过"), parse_answer(GOOD), {"开心", "难过"})
    assert "group" not in patch and patch["suggested_group"] == "开心"


def test_plan_patch_ignores_unknown_group():
    patch = plan_patch(_sticker(), parse_answer(GOOD), {"别的"})
    assert "group" not in patch and patch["suggested_group"] == ""


def test_parse_answer_needs_caption_and_strips_fences():
    assert parse_answer("```json\n" + GOOD + "\n```")["name"] == "开心转圈"
    assert parse_answer('{"name":"x","caption":""}') == {}
    assert parse_answer("nope") == {}


def test_prompt_lists_zone_groups_and_current_group():
    text = build_prompt({"开心": "好消息时用"}, "开心", animated=True)
    assert "开心：好消息时用" in text and "动图" in text and "现在在「开心」" in text


def test_encode_tiles_animated_gif(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    frames = [pil.new("RGB", (300, 200), color) for color in ("red", "green", "blue", "white", "black")]
    path = tmp_path / "a.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=50)
    url, animated = encode_image(path)
    assert animated and url.startswith("data:image/png;base64,")
    still = tmp_path / "b.png"
    pil.new("RGBA", (1000, 600), (0, 0, 0, 0)).save(still)
    url, animated = encode_image(still)
    assert not animated


def test_moving_or_ignoring_clears_the_suggestion(tmp_path):
    lib, labeler, _, ids = _setup(tmp_path, count=2)
    lib.create_group("开心", "", lib.active_zone())
    lib.create_group("难过", "", lib.active_zone())
    for sticker_id in ids:
        lib.update(sticker_id, group="难过")
    labeler.start_missing()
    _run(labeler, 2)
    assert lib.get(ids[0]).suggested_group == "开心"
    lib.update(ids[0], group="开心")
    assert lib.get(ids[0]).suggested_group == ""
    assert lib.clear_suggestion(ids[1]) and lib.get(ids[1]).suggested_group == ""
