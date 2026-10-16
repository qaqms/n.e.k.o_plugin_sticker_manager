"""Speech pacing and candidate lookup must not wait on avoidable role probes."""

from copy import deepcopy
from unittest.mock import AsyncMock

from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.configuration import StickerManagerSettings

from .test_host_reply_tail import CONTEXT


def _surface(plugin, *, modern=True, reject_context=False):
    metas = {
        "sticker_send": {
            "name": "sticker_send", "description": "send", "parameters": {}, "role": None,
        },
        "sticker_list": {
            "name": "sticker_list", "description": "lookup", "parameters": {"required": []},
            "role": "K", "timeout_seconds": 10.0,
        },
    }
    calls = []
    plugin.list_llm_tools = lambda: list(metas.values())
    plugin.unregister_llm_tool = lambda name: metas.pop(name, None) is not None

    def register(*, name, description, parameters, handler, timeout, role, reply_tail=False):
        calls.append((name, reply_tail))
        if reject_context and name == "sticker_list" and reply_tail:
            return False
        metas[name] = {
            "name": name, "description": description, "parameters": parameters,
            "handler": handler, "timeout_seconds": timeout, "role": role, "reply_tail": reply_tail,
        }
        return True

    def old_register(*, name, description, parameters, handler, timeout, role):
        return register(
            name=name, description=description, parameters=parameters,
            handler=handler, timeout=timeout, role=role,
        )

    plugin.register_llm_tool = register if modern else old_register
    return metas, calls


def test_both_tools_opt_into_original_role_context_once(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    metas, calls = _surface(plugin)
    before = deepcopy(metas["sticker_list"])
    assert plugin._apply_send_tool_surface()
    assert calls == [("sticker_list", True), ("sticker_send", True)]
    lookup = metas["sticker_list"]
    assert lookup["handler"] == plugin.tool_sticker_list
    for name in ("description", "parameters", "role", "timeout_seconds"):
        assert lookup[name] == before[name]
    assert plugin._apply_send_tool_surface()
    assert len(calls) == 2


def test_unsupported_sdk_keeps_lookup_registered_without_new_flag(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    metas, calls = _surface(plugin, modern=False)
    before = deepcopy(metas["sticker_list"])
    assert plugin._apply_send_tool_surface()
    assert calls == [("sticker_send", False)]
    assert metas["sticker_list"] == before


def test_failed_context_upgrade_restores_lookup_and_keeps_retry_flag(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    metas, calls = _surface(plugin, reject_context=True)
    assert not plugin._refresh_send_tool_surface()
    assert calls == [("sticker_list", True), ("sticker_list", False)]
    assert metas["sticker_list"]["handler"] == plugin.tool_sticker_list
    assert metas["sticker_list"]["reply_tail"] is False
    assert "sticker_send" in metas and plugin._surface_dirty


def test_original_role_lookup_avoids_bus_and_current_role_probes(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(enabled=True)
    plugin._library.load()
    first, error = plugin._library.add(data=PNG_BYTES, desc="first", tags=[], group="happy")
    assert not error
    second, error = plugin._library.add(data=PNG_BYTES + b"second", desc="second", tags=[], group="happy")
    assert not error
    plugin._library.append_usage(
        {"id": first.id, "lanlan": "K", "ok": True, "at": 1.0}, keep=200,
    )
    plugin._lanlan._from_bus = AsyncMock(side_effect=AssertionError("unnecessary bus read"))
    plugin._lanlan._from_host = AsyncMock(side_effect=AssertionError("unnecessary role HTTP"))
    result = run_async(plugin.tool_sticker_list(
        group="happy", _ctx={"host_reply": CONTEXT, "lanlan_name": "other-role"},
    ))
    assert result["candidate_ids"] == [second.id]
    plugin._lanlan._from_bus.assert_not_awaited()
    plugin._lanlan._from_host.assert_not_awaited()
    assert not host.push.calls


def test_legacy_lookup_still_resolves_role_with_existing_fallback(tmp_path, run_async):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(enabled=True)
    plugin._library.load()
    plugin._lanlan._from_bus = AsyncMock(return_value="K")
    plugin._lanlan._from_host = AsyncMock(side_effect=AssertionError("unnecessary role HTTP"))
    run_async(plugin.tool_sticker_list())
    plugin._lanlan._from_bus.assert_awaited_once()
    plugin._lanlan._from_host.assert_not_awaited()
