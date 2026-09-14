"""入口面测试：add/update/remove/send/list/preview/history/switch + 两个 llm_tool。

用真文件（tmp_path）+ 假宿主，走 handler 本体而不是内部服务，
钉的是"面板/模型看到的形状"。
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace

from conftest import GIF_BYTES, JPEG_BYTES, NOT_AN_IMAGE, PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings


def _make(tmp_path, *, enabled=True):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": enabled}}),
    )
    plugin, host = build_plugin(host)
    settings = StickerManagerSettings(
        enabled=enabled, send=SendSettings(), storage=StorageSettings()
    )
    plugin._settings = settings
    return plugin, host


def _ctx(lanlan: str) -> dict:
    return {"_ctx": {"lanlan_name": lanlan}}


async def _add(plugin, desc="笑", tags="开心,猫", data=PNG_BYTES):
    return await plugin.add_entry(
        data_base64=base64.b64encode(data).decode("ascii"), desc=desc, tags=tags
    )


class TestAddEntry:
    def test_add_roundtrip(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(_add(plugin))
        assert result.is_ok()
        payload = result.value
        assert payload["note"] == "sticker_added"
        assert payload["desc"] == "笑"
        # 目录里能搜到，标签被拆开
        listed = run_async(plugin.list_entry(query="笑"))
        assert listed.value["count"] == 1
        assert listed.value["stickers"][0]["tags"] == ["开心", "猫"]

    def test_rejects_non_image(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(_add(plugin, data=NOT_AN_IMAGE))
        assert not result.is_ok()
        assert str(result.error) == "invalid_image"

    def test_requires_desc(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(_add(plugin, desc="   "))
        assert not result.is_ok()
        assert str(result.error) == "desc_required"

    def test_rejects_undecodable_base64(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(
            plugin.add_entry(data_base64="\x00not base64 at all!!", desc="坏", tags="")
        )
        assert not result.is_ok()
        assert str(result.error) == "image_undecodable"

    def test_management_works_while_disabled(self, tmp_path, run_async):
        # fail-closed 只管"发"，不管"藏"：开关关闭时收藏仍要能成功
        plugin, _host = _make(tmp_path, enabled=False)
        result = run_async(_add(plugin))
        assert result.is_ok()


class TestUpdateRemove:
    def test_update_desc_and_disabled(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        added = run_async(_add(plugin))
        sid = added.value["id"]
        updated = run_async(plugin.update_entry(id=sid, desc="大哭", disabled=True, **_ctx("K")))
        assert updated.is_ok()
        # 默认视图：禁用条目消失；include_disabled 能捞回并看到新描述
        assert run_async(plugin.list_entry()).value["stickers"] == []
        listed = run_async(plugin.list_entry(include_disabled=True))
        row = listed.value["stickers"][0]
        assert row["desc"] == "大哭" and row["disabled"] is True

    def test_update_missing(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(plugin.update_entry(id="ghost", desc="x"))
        assert not result.is_ok()
        assert str(result.error) == "sticker_not_found"

    def test_remove(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        assert run_async(plugin.remove_entry(id=sid)).is_ok()
        assert run_async(plugin.list_entry()).value["count"] == 0
        assert not (tmp_path / "library" / "stickers" / f"{sid}.png").exists()


class TestSendAndPreview:
    def test_panel_send_and_history(self, tmp_path, run_async):
        plugin, host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        result = run_async(plugin.send_entry(id=sid, **_ctx("K")))
        assert result.is_ok()
        (call,) = host.push.calls
        assert call["target_lanlan"] == "K"
        assert call["visibility"] == ["chat"] and call["ai_behavior"] == "read"
        history = run_async(plugin.history_entry(limit=10))
        assert history.value["usage"][0]["id"] == sid
        assert history.value["usage"][0]["source"] == "panel"

    def test_send_respects_cooldown(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        assert run_async(plugin.send_entry(id=sid, **_ctx("K"))).is_ok()
        second = run_async(plugin.send_entry(id=sid, **_ctx("K")))
        assert not second.is_ok()
        assert str(second.error) == "send_cooldown"

    def test_send_blocked_when_disabled(self, tmp_path, run_async):
        plugin, host = _make(tmp_path, enabled=False)
        sid = run_async(_add(plugin, data=PNG_BYTES)).value["id"]
        result = run_async(plugin.send_entry(id=sid, **_ctx("K")))
        assert not result.is_ok()
        assert str(result.error) == "not_enabled"
        assert host.push.calls == []

    def test_preview_returns_data_url(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin, data=GIF_BYTES)).value["id"]
        result = run_async(plugin.preview_entry(id=sid))
        assert result.is_ok()
        assert result.value["data_url"].startswith("data:image/gif;base64,")

    def test_preview_missing_file(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        plugin._library.image_path(plugin._library.get(sid)).unlink()
        result = run_async(plugin.preview_entry(id=sid))
        assert not result.is_ok()
        assert str(result.error) == "sticker_file_missing"


class TestSwitchEntry:
    def test_switch_writes_config_and_reloads(self, tmp_path, run_async):
        plugin, host = _make(tmp_path, enabled=False)
        result = run_async(plugin.switch_entry(enabled=True))
        assert result.is_ok() and result.value["enabled"] is True
        assert host.config.writes == [("sticker_manager.enabled", True)]
        assert plugin._settings.enabled is True

    def test_switch_config_failure_reports(self, tmp_path, run_async):
        plugin, host = _make(tmp_path)
        host.config.set_error = RuntimeError("boom")
        result = run_async(plugin.switch_entry(enabled=False))
        assert not result.is_ok()
        assert str(result.error) == "config_unavailable"


class TestTools:
    def test_sticker_list_enabled(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin, desc="猫咪开心挥手", tags="开心"))
        out = run_async(plugin.tool_sticker_list(query="", **_ctx("K")))
        assert out["ok"] is True
        assert "猫咪开心挥手" in out["catalog"]

    def test_sticker_list_disabled_master_switch(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path, enabled=False)
        out = run_async(plugin.tool_sticker_list())
        assert out == {"ok": False, "reason": "not_enabled"}

    def test_sticker_send_by_id(self, tmp_path, run_async):
        plugin, host = _make(tmp_path)
        sid = run_async(_add(plugin, desc="笑")).value["id"]
        out = run_async(plugin.tool_sticker_send(sticker_id=sid, **_ctx("K")))
        assert out["ok"] is True
        assert out["sent"] == sid
        assert host.push.calls

    def test_sticker_send_by_query(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin, desc="无语凝噎", tags="无语"))
        out = run_async(plugin.tool_sticker_send(query="无语", **_ctx("K")))
        assert out["ok"] is True

    def test_sticker_send_no_match(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        out = run_async(plugin.tool_sticker_send(query="不存在的", **_ctx("K")))
        assert out["ok"] is False
        assert out["reason"] == "no_match"

    def test_sticker_send_skips_disabled_pick(self, tmp_path, run_async):
        # 模型点名要一张被禁用的图：不直发，回退到搜索；搜索也不含禁用 → no_match
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin, desc="被禁的")).value["id"]
        run_async(plugin.update_entry(id=sid, disabled=True))
        out = run_async(plugin.tool_sticker_send(sticker_id=sid, **_ctx("K")))
        assert out["ok"] is False
        assert out["reason"] == "no_match"

    def test_failed_tool_send_enters_ledger(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        plugin._settings = replace(plugin._settings, send=SendSettings(cooldown_sec=3600.0))
        assert run_async(plugin.tool_sticker_send(sticker_id=sid, **_ctx("K")))["ok"] is True
        blocked = run_async(plugin.tool_sticker_send(sticker_id=sid, **_ctx("K")))
        assert blocked["ok"] is False
        assert blocked["reason"] == "send_cooldown"
        codes = [e.get("code") for e in plugin._library.read_usage() if not e.get("ok")]
        assert "send_cooldown" in codes


class TestDashboardContext:
    def test_shape(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin))
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert payload["enabled"] is True
        assert payload["lanlan"] == "K"
        assert payload["counts"]["total"] == 1
        assert payload["counts"]["enabled"] == 1
        assert payload["stickers"][0]["desc"] == "笑"
        assert "config" in payload and "error_code" not in payload

    def test_io_error_surfaces_as_code(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        (tmp_path / "library" / "catalog.json").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "library" / "catalog.json").write_text("{ broken", encoding="utf-8")
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert payload["error_code"] == "library_io_error"


class TestDedupAndRepair:
    def test_add_duplicate_returns_stable_code(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        assert run_async(_add(plugin)).is_ok()
        again = run_async(_add(plugin, desc="换个字也一样"))
        assert not again.is_ok()
        assert str(again.error) == "duplicate_image"

    def test_repair_entry_reports_counts(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        keep = run_async(_add(plugin)).value["id"]
        broken = run_async(_add(plugin, desc="坏", data=JPEG_BYTES)).value["id"]
        (tmp_path / "library" / "stickers" / f"{broken}.jpg").unlink()
        result = run_async(plugin.repair_entry())
        assert result.is_ok()
        payload = result.value
        assert payload["note"] == "library_repaired"
        assert payload["removed_entries"] == 1
        assert payload["purged_files"] == 0
        assert payload["backfilled_hashes"] == 0
        listed = run_async(plugin.list_entry())
        assert [s["id"] for s in listed.value["stickers"]] == [keep]

    def test_sticker_rows_carry_sha256(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        row = run_async(plugin.list_entry()).value["stickers"][0]
        assert row["id"] == sid
        assert row["sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()


class TestInboxEntry:
    def test_import_inbox_counts_and_context_pending(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        inbox = tmp_path / "library" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "挥手_cat.png").write_bytes(PNG_BYTES)
        (inbox / "hi.gif").write_bytes(GIF_BYTES)
        (inbox / "hi_copy.gif").write_bytes(GIF_BYTES)  # 与上一张内容相同 → 重复
        pending = run_async(plugin.dashboard_context(**_ctx("K")))
        assert pending["inbox"]["pending"] == 3
        result = run_async(plugin.import_inbox_entry(tags="批量,导入"))
        assert result.is_ok()
        payload = result.value
        assert payload["note"] == "inbox_imported"
        assert payload["imported"] == 2
        assert payload["duplicates"] == 1
        assert payload["rejected"] == 0 and payload["failed"] == 0
        listed = run_async(plugin.list_entry())
        descs = {row["desc"] for row in listed.value["stickers"]}
        assert descs == {"挥手 cat", "hi"}
        assert all(row["tags"] == ["批量", "导入"] for row in listed.value["stickers"])
        after = run_async(plugin.dashboard_context(**_ctx("K")))
        assert after["inbox"]["pending"] == 0
        assert after["inbox"]["path"]

    def test_import_inbox_works_while_disabled(self, tmp_path, run_async):
        # fail-closed 只管"发"，收的通道（含收件箱）不受开关影响
        plugin, _host = _make(tmp_path, enabled=False)
        inbox = tmp_path / "library" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "hi.png").write_bytes(PNG_BYTES)
        result = run_async(plugin.import_inbox_entry())
        assert result.is_ok() and result.value["imported"] == 1
