import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sticker_package_builder", ROOT / "tools" / "build_package.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def _stage_fixture(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (root / "plugin.meta.json").write_text('{"source_files": ["tests/absent.py"]}', encoding="utf-8")
    host = tmp_path / "host"
    python = host / ".venv" / ("Scripts/python.exe" if builder.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(builder, "ROOT", root)
    return root, host


def test_metadata_is_derived_from_payload_and_same_bytes_are_archived(tmp_path, monkeypatch):
    root, host = _stage_fixture(tmp_path, monkeypatch)
    original = (root / "__init__.py").read_bytes()

    def probe(args, **kwargs):
        staged = Path(args[-1])
        assert not (staged / "plugin.meta.json").exists()
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert "derive_plugin_metadata" in args[2]
        assert "read_packaged_metadata" in args[2]
        (staged / "plugin.meta.json").write_text(json.dumps({"source_files": ["__init__.py"]}), encoding="utf-8")
        (root / "__init__.py").write_text("value = 2\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(builder.subprocess, "run", probe)
    entries = dict(builder.stage_plugin_entries(list(root.iterdir()), host))
    assert entries["plugins/sticker_manager/__init__.py"] == original
    assert json.loads(entries["plugins/sticker_manager/plugin.meta.json"])["source_files"] == ["__init__.py"]


def test_probe_failure_refuses_package_metadata(tmp_path, monkeypatch):
    root, host = _stage_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(builder.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="probe failed"
    ))
    with pytest.raises(SystemExit, match="probe failed"):
        builder.stage_plugin_entries(list(root.iterdir()), host)


def test_mismatched_file_list_is_rejected(tmp_path, monkeypatch):
    root, host = _stage_fixture(tmp_path, monkeypatch)

    def probe(args, **kwargs):
        (Path(args[-1]) / "plugin.meta.json").write_text('{"source_files": ["tests/absent.py"]}', encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(builder.subprocess, "run", probe)
    with pytest.raises(SystemExit, match="file list"):
        builder.stage_plugin_entries(list(root.iterdir()), host)


def test_missing_host_is_an_explicit_build_failure(tmp_path):
    with pytest.raises(SystemExit, match="Host Python not found"):
        builder.stage_plugin_entries([], tmp_path / "missing")
