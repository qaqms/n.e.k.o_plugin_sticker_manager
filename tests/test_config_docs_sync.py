"""三处同源门：plugin.toml 业务段 == config.example.toml == core/configuration.py 默认值。

沿用 our_life 的教训：加配置项时必须三处一起改，这个门负责抓"改了一处忘两处"。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _flatten(prefix: str, table: dict) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in table.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(dotted, value))
        else:
            out[dotted] = value
    return out


def _manifest_section() -> dict[str, object]:
    data = _load(ROOT / "plugin.toml")
    return _flatten("sticker_manager", data["sticker_manager"])


def _example_section() -> dict[str, object]:
    data = _load(ROOT / "config.example.toml")
    assert "plugin" not in data, "config.example.toml 不得含 [plugin] 表"
    return _flatten("sticker_manager", data["sticker_manager"])


def _dataclass_defaults() -> dict[str, object]:
    settings = StickerManagerSettings.defaults()
    return {
        "sticker_manager.enabled": settings.enabled,
        "sticker_manager.send.cooldown_sec": float(settings.send.cooldown_sec),
        "sticker_manager.send.inline_max_bytes": settings.send.inline_max_bytes,
        "sticker_manager.send.animated_via_upload": settings.send.animated_via_upload,
        "sticker_manager.storage.catalog_limit_for_model": settings.storage.catalog_limit_for_model,
        "sticker_manager.storage.usage_history_keep": settings.storage.usage_history_keep,
    }


def test_manifest_and_example_are_key_identical():
    assert set(_manifest_section()) == set(_example_section())


def test_manifest_and_example_are_value_identical():
    manifest, example = _manifest_section(), _example_section()
    for key in manifest:
        assert manifest[key] == example[key], f"value drift at {key}"


def test_manifest_matches_dataclass_defaults():
    manifest = _manifest_section()
    defaults = _dataclass_defaults()
    assert set(manifest) == set(defaults)
    for key, value in defaults.items():
        actual = manifest[key]
        if isinstance(value, float):
            assert abs(float(actual) - value) < 1e-9, f"float drift at {key}"
        else:
            assert actual == value, f"default drift at {key}"


def test_dataclass_section_dataclasses_have_no_undeclared_extra():
    """dataclass 加了键但没进 _dataclass_defaults —— 用字段数钉住。"""
    assert len(SendSettings.__dataclass_fields__) == 3
    assert len(StorageSettings.__dataclass_fields__) == 2
