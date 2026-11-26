"""Panel entries stay callable without joining automatic Agent assessment."""

from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager import StickerManagerPlugin


def _entry_metadata():
    entries = {}
    for name, member in vars(StickerManagerPlugin).items():
        if not name.endswith("_entry"):
            continue
        for declaration in getattr(member, "__neko_stub_meta__", []):
            kwargs = declaration["kwargs"]
            if "id" in kwargs and "name" in kwargs:
                entries[kwargs["id"]] = kwargs
    return entries


def test_all_management_entries_opt_out_of_automatic_agent_routing():
    entries = _entry_metadata()
    assert len(entries) == 36
    assert {
        "send", "list", "preview", "switch", "set_eagerness", "set_reminder_interval", "set_send_cooldown",
        "set_eager_attachment_turns", "test_selection", "test_vision", "auto_label", "add_batch", "set_every_reply", "set_send_probability",
    } <= entries.keys()
    assert all(entry["metadata"]["agent_auto"] is False for entry in entries.values())


def test_main_chat_tools_keep_their_public_names_and_schemas():
    for member, name in (
        (StickerManagerPlugin.tool_sticker_list, "sticker_list"),
        (StickerManagerPlugin.tool_sticker_send, "sticker_send"),
    ):
        declarations = getattr(member, "__neko_stub_meta__", [])
        tools = [raw["kwargs"] for raw in declarations if "parameters" in raw["kwargs"]]
        assert len(tools) == 1
        assert tools[0]["name"] == name
        assert "metadata" not in tools[0]
        assert tools[0].get("role") is None
        assert "group" in tools[0]["parameters"]["properties"]


def test_hidden_panel_entry_still_dispatches_by_id(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    assert run_async(plugin.switch_entry(enabled=True)).is_ok()
    plugin._library.load()
    sticker, error = plugin._library.add(data=PNG_BYTES, desc="happy", tags=[])
    assert not error

    result = run_async(plugin.send_entry(id=sticker.id, _ctx={"lanlan_name": "K"}))

    assert result.is_ok() and result.value["id"] == sticker.id
    assert host.push.calls[0]["visibility"] == ["chat"]
    assert plugin._library.read_usage()[0]["source"] == "panel"


def test_every_declared_id_is_unique_across_entries_timers_and_tools():
    # 宿主把定时器和入口放进同一张 entry 表：同名时定时器盖掉入口，
    # 面板按钮会被拒（plugin_ui_action_forbidden）。
    seen = {}
    for name, member in vars(StickerManagerPlugin).items():
        for declaration in getattr(member, "__neko_stub_meta__", []):
            declared = declaration["kwargs"].get("id")
            if not declared:
                continue
            assert declared not in seen or seen[declared] == name, (declared, seen.get(declared), name)
            seen[declared] = name
    assert "auto_label" in seen and "auto_label_tick" in seen
