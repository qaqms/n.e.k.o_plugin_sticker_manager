"""Probability-based sending: one-time migration of existing installs and the panel entry."""
from __future__ import annotations

import json

from conftest import FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.configuration import StickerManagerSettings  # pyright: ignore[reportMissingImports]

OLD = {"eagerness": "eager", "cooldown_sec": 20.0, "probability": 1.0, "every_reply": True,
       "eager_attachment_turns": 3}


def _plugin(tmp_path, send=None):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True, "send": dict(send or {})}}),
    )
    plugin, host = build_plugin(host)
    plugin._settings = StickerManagerSettings.from_config(host.config.data)
    return plugin, host


def test_migration_overwrites_old_send_settings_once(tmp_path, run_async):
    plugin, host = _plugin(tmp_path, OLD)
    run_async(plugin._migrate_send_settings())
    send = host.config.data["sticker_manager"]["send"]
    assert send["probability"] == 0.5 and send["cooldown_sec"] == 0.0
    assert send["every_reply"] is False and send["eagerness"] == "natural"
    assert send["eager_attachment_turns"] == 1
    assert json.loads((tmp_path / "settings_version.json").read_text(encoding="utf-8"))["version"] == 2

    # Owner changes after migration survive later starts.
    host.config.data["sticker_manager"]["send"]["probability"] = 0.8
    host.config.writes.clear()
    run_async(plugin._migrate_send_settings())
    assert host.config.writes == []
    assert host.config.data["sticker_manager"]["send"]["probability"] == 0.8


def test_migration_failure_leaves_no_marker_and_retries(tmp_path, run_async):
    plugin, host = _plugin(tmp_path, OLD)
    host.config.set_error = RuntimeError("boom")
    run_async(plugin._migrate_send_settings())
    assert not (tmp_path / "settings_version.json").exists()
    host.config.set_error = None
    run_async(plugin._migrate_send_settings())
    assert host.config.data["sticker_manager"]["send"]["probability"] == 0.5


def test_corrupt_marker_reapplies(tmp_path, run_async):
    plugin, host = _plugin(tmp_path, OLD)
    (tmp_path / "settings_version.json").write_text("{not json", encoding="utf-8")
    run_async(plugin._migrate_send_settings())
    assert host.config.data["sticker_manager"]["send"]["cooldown_sec"] == 0.0


def test_set_send_probability_persists_and_reloads(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    result = run_async(plugin.set_send_probability_entry(probability=0.3))
    assert result.is_ok()
    assert result.value == {"note": "send_probability_set", "probability": 0.3}
    assert host.config.writes == [("sticker_manager.send.probability", 0.3)]
    assert plugin._settings.send.probability == 0.3


def test_set_send_probability_rejects_invalid(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    for bad in (-0.1, 1.5, float("nan"), True, "0.5"):
        result = run_async(plugin.set_send_probability_entry(probability=bad))
        assert not result.is_ok() and str(result.error) == "invalid_value"
    assert host.config.writes == []


def test_set_send_probability_config_failure(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    host.config.set_error = RuntimeError("boom")
    result = run_async(plugin.set_send_probability_entry(probability=0.2))
    assert not result.is_ok() and str(result.error) == "config_unavailable"
