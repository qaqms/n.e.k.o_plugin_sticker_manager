"""Intent selection contracts; these tests do not simulate an LLM's judgment."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.catalog import SELECTION_MATCH_RULE, Sticker, resolve_send_target, search_intent_candidates
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.core.eagerness import INTERNAL_SELECTION_NOTE
from sticker_manager.core.tool_surface import build_send_tool_description
from sticker_manager.services.bundled_pack import open_pack
from sticker_manager.services.library import Library

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "official/official_pack.zip"


def _setup(tmp_path):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(
        enabled=True, send=SendSettings(cooldown_sec=0, recent_dedup_count=5),
    )
    plugin._library.load()
    return plugin, host


def _add(plugin, label, *, group="mixed", caption="", tags=()):
    sticker, error = plugin._library.add(
        data=PNG_BYTES + label.encode(), desc=label, tags=list(tags), group=group, caption=caption,
    )
    assert not error
    return sticker


@pytest.mark.parametrize("selector", [{"group": "mixed"}, {"query": "难过"}, {"query": "难过", "force": True}])
def test_one_candidate_never_sends_until_exact_id_is_chosen(tmp_path, run_async, selector):
    plugin, host = _setup(tmp_path)
    sticker = _add(plugin, "难过", caption="自己委屈落泪，想被安慰。")
    out = run_async(plugin.tool_sticker_send(**selector, _ctx={"lanlan_name": "K"}))
    assert out["ok"] and out["sent"] == "" and out["candidate_ids"] == [sticker.id]
    assert sticker.caption in out["candidates"]
    assert all(word in out["hint"] for word in ("安慰对方", "自己难过", "嘲笑对方", "没有合适的就纯文字"))
    assert not host.push.calls and not plugin._library.read_usage()
    assert plugin._library.get(sticker.id).use_count == 0 and plugin._runstats.sent == 0
    confirmed = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
    assert confirmed["sent"] == sticker.id and len(host.push.calls) == 1


@pytest.mark.parametrize("tool_name", ["tool_sticker_list", "tool_sticker_send"])
def test_group_and_query_are_intersected_and_miss_never_falls_back(tmp_path, run_async, tool_name):
    plugin, host = _setup(tmp_path)
    wanted = _add(plugin, "摸摸", group="gentle", caption="自己被摸头，舒服地眯眼。")
    other = _add(plugin, "摸摸也是这个词", group="other")
    method = getattr(plugin, tool_name)
    hit = run_async(method(group="gentle", query="摸摸"))
    assert hit["candidate_ids"] == [wanted.id] and other.id not in hit["candidate_ids"]
    miss = run_async(method(group="gentle", query="不存在的语义"))
    assert not miss.get("candidate_ids") and not miss.get("sent")
    assert not host.push.calls and not plugin._library.read_usage()


@pytest.mark.parametrize("extra", [{"group": "mixed"}, {"query": "难过"}, {"group": "mixed", "query": "难过"}])
def test_invalid_explicit_id_never_substitutes_another_image(tmp_path, run_async, extra):
    plugin, host = _setup(tmp_path)
    _add(plugin, "难过")
    out = run_async(plugin.tool_sticker_send(sticker_id="missing", **extra))
    assert not out["ok"] and out["reason"] == "no_match"
    assert not host.push.calls


def test_list_excludes_recent_disabled_and_other_zone_candidates(tmp_path, run_async):
    plugin, host = _setup(tmp_path)
    recent = _add(plugin, "recent")
    disabled = _add(plugin, "disabled")
    wanted = _add(plugin, "wanted")
    plugin._library.update(disabled.id, disabled=True)
    plugin._library.append_usage({"id": recent.id, "ok": True, "lanlan": "K", "at": 1}, keep=200)
    away, error = plugin._library.create_zone("away", "")
    assert not error
    assert not plugin._library.add(data=PNG_BYTES + b"away", desc="away", tags=[], zone=away)[1]
    out = run_async(plugin.tool_sticker_list(group="mixed", _ctx={"lanlan_name": "K"}))
    assert out["candidate_ids"] == [wanted.id]
    assert not host.push.calls


def test_keyword_union_supports_short_intent_terms_without_claiming_a_send():
    pool = [
        Sticker("a", "a.png", "", caption="自己被摸头，享受宠爱。", tags=["自己被摸头", "宠爱"]),
        Sticker("b", "b.png", "", caption="自己落泪。", tags=["自己难过"]),
    ]
    assert [row.id for row in search_intent_candidates(pool, "摸头，宠爱")] == ["a"]
    assert {row.id for row in search_intent_candidates(pool, "摸头 难过")} == {"a", "b"}
    chosen, candidates = resolve_send_target(pool, "难过")
    assert chosen is None and [row.id for row in candidates] == ["b"]


def test_positive_usage_tags_outrank_negative_examples_in_caption():
    pool = [
        Sticker("happy", "h.png", "", caption="庆祝，不用于难过的回复。", tags=["庆祝"]),
        Sticker("sad", "s.png", "", caption="自己落泪。", tags=["自己难过"]),
    ]
    assert search_intent_candidates(pool, "难过")[0].id == "sad"


def test_corrupt_catalog_does_not_offer_stale_candidates(tmp_path, run_async):
    plugin, host = _setup(tmp_path)
    _add(plugin, "one")
    plugin._library.catalog_path.write_text("broken", encoding="utf-8")
    assert not plugin._library.load(force=True).ok
    out = run_async(plugin.tool_sticker_list())
    assert not out["ok"] and out["reason"] == "library_io_error"
    assert not host.push.calls


@pytest.mark.parametrize("tool_name", ["tool_sticker_list", "tool_sticker_send"])
@pytest.mark.parametrize("outcome", ["candidate", "empty", "disabled", "unreadable"])
def test_model_receipts_keep_selection_private_even_without_a_send(tmp_path, run_async, tool_name, outcome):
    plugin, host = _setup(tmp_path)
    sticker = _add(plugin, "期待", caption="自己期待接下来的好事。")
    query = "期待"
    if outcome == "empty":
        query = "无此用途"
    elif outcome == "disabled":
        plugin._settings = StickerManagerSettings(enabled=False)
    elif outcome == "unreadable":
        plugin._library.catalog_path.write_text("broken", encoding="utf-8")
        assert not plugin._library.load(force=True).ok
    result = run_async(getattr(plugin, tool_name)(query=query))
    assert INTERNAL_SELECTION_NOTE in result["hint"]
    if outcome == "candidate":
        assert result["candidate_ids"] == [sticker.id]
        assert SELECTION_MATCH_RULE in result["hint"]
    else:
        assert not result.get("candidate_ids")
    assert not host.push.calls


def test_success_and_refusal_continue_privately_without_repeating_text(tmp_path, run_async, caplog):
    caplog.set_level("INFO")
    plugin, host = _setup(tmp_path)
    sticker = _add(plugin, "期待")
    sent = run_async(plugin.tool_sticker_send(sticker_id=sticker.id))
    refused = run_async(plugin.tool_sticker_send(sticker_id=sticker.id))
    assert sent["sent"] == sticker.id and refused["reason"] == "recent_repeat"
    assert len(host.push.calls) == 1 and host.push.calls[0]["parts"][0]["type"] == "image"
    for result in (sent, refused):
        assert INTERNAL_SELECTION_NOTE in result["hint"]
    assert "本轮选图已完成" in sent["note"] and "若已说完就结束" in sent["note"]
    assert f"outcome=sent id={sticker.id}" in caplog.text


def test_agent_candidates_and_both_registered_tools_keep_selection_private(tmp_path, run_async):
    plugin, host = _setup(tmp_path)
    sticker = _add(plugin, "期待")
    result = run_async(plugin.send_entry(query="期待")).value
    assert result["candidate_ids"] == [sticker.id] and INTERNAL_SELECTION_NOTE in result["hint"]
    assert not host.push.calls
    for tool in (plugin.tool_sticker_list, plugin.tool_sticker_send):
        metadata = next(record["kwargs"] for record in tool.__neko_stub_meta__ if "parameters" in record["kwargs"])
        assert INTERNAL_SELECTION_NOTE in metadata["description"]
        assert SELECTION_MATCH_RULE in metadata["description"]
    for tier in ("reserved", "natural", "eager"):
        surface = build_send_tool_description(plugin._send_tool_catalog(), tier)
        assert INTERNAL_SELECTION_NOTE in surface and SELECTION_MATCH_RULE in surface


def test_reviewed_builtin_labels_match_shipped_pack_and_keep_role_distinctions():
    reviewed = json.loads((ROOT / "tools/official_labels_v3.json").read_text(encoding="utf-8"))
    with open_pack(PACK) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["pack_version"] == reviewed["pack_version"] == 3
    rows = {row["file"].split("_", 1)[0]: row for row in manifest["stickers"]}
    assert set(rows) == set(reviewed["stickers"]) and len(rows) == 190
    for key, patch in reviewed["stickers"].items():
        assert rows[key]["caption"] == patch["caption"] and rows[key]["tags"] == patch["tags"]
    assert "自己被摸头" in rows["115"]["tags"] and "自己被摸头" in rows["116"]["tags"]
    assert "自己嘲笑对方" in rows["090"]["tags"] and "自己开心" in rows["089"]["tags"]
    assert "自己难过" in rows["037"]["tags"] and "鼓励对方" in rows["027"]["tags"]
    pool = [Sticker.from_dict({**row, "id": key}) for key, row in rows.items()]
    assert search_intent_candidates(pool, "难过")[0].id == "037"
    _, happy = resolve_send_target(pool, "开心")
    assert "090" not in {sticker.id for sticker in happy}


def test_v2_label_upgrade_preserves_ids_owner_edits_usage_and_deleted_images(tmp_path):
    with open_pack(PACK) as archive:
        raw = json.loads(archive.read("manifest.json"))
        rows = [row for row in raw["stickers"] if row["file"].startswith(("025_", "090_", "115_"))]
        old_rows = [{**row, "caption": "old caption", "tags": ["old"]} for row in rows]
        old_pack = tmp_path / "v2.zip"
        with zipfile.ZipFile(old_pack, "w") as old:
            old.writestr("manifest.json", json.dumps({"version": 3, "pack_version": 2, "stickers": old_rows}))
            for row in rows:
                old.writestr("stickers/" + row["file"], archive.read("stickers/" + row["file"]))
    library = Library(tmp_path / "library")
    assert library.load().ok
    assert library.seed_official(old_pack)["imported"] == 3
    stickers = {sticker.sha256: sticker for sticker in library.all()}
    edited, untouched, deleted = [stickers[row["sha256"]] for row in rows]
    library.update(edited.id, caption="主人亲手写的用途")
    library.append_usage({"id": untouched.id, "ok": True, "lanlan": "K", "at": 1}, keep=200)
    library.remove(deleted.id)
    before_ids = {sticker.id for sticker in library.all()}
    result = library.refresh_official_labels(PACK)
    assert result["status"] == "refreshed" and result["version"] == 3 and result["images_updated"] == 0
    assert {sticker.id for sticker in library.all()} == before_ids
    assert library.get(edited.id).caption == "主人亲手写的用途"
    assert library.get(untouched.id).caption == rows[1]["caption"]
    assert library.read_usage()[0]["id"] == untouched.id
    assert library.refresh_official_labels(PACK) == {"status": "current", "version": 3}
