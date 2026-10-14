import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sticker_package_builder", ROOT / "tools" / "build_package.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
ISOLATION_SPEC = importlib.util.spec_from_file_location("sticker_host_isolation_test", ROOT / "tools/host_isolation.py")
isolation = importlib.util.module_from_spec(ISOLATION_SPEC)
ISOLATION_SPEC.loader.exec_module(isolation)


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
        assert kwargs["cwd"] != host
        assert Path(kwargs["env"]["NEKO_STORAGE_SELECTED_ROOT"]).is_relative_to(
            Path(kwargs["env"]["STICKER_ISOLATION_ROOT"])
        )
        assert (kwargs["cwd"] / "sitecustomize.py").is_file()
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


def test_snapshot_copies_support_source_not_installed_plugins_or_caches(tmp_path):
    host = tmp_path / "host"
    for name in (
        "plugin/sdk/base.py",
        "plugin/sdk/hosted-ui/index.d.ts",
        "plugin/plugins/installed/__init__.py",
        "plugin/tests/test_host.py",
        "utils/__pycache__/stale.py",
        "config/api_providers.json",
        "utils/large-asset.zip",
    ):
        source = host / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(name, encoding="utf-8")
    snapshot = tmp_path / "isolated" / "snapshot"
    isolation.copy_host_sources(host, snapshot)
    assert (snapshot / "plugin/sdk/base.py").is_file()
    assert (snapshot / "plugin/sdk/hosted-ui/index.d.ts").is_file()
    assert (snapshot / "config/api_providers.json").is_file()
    assert not (snapshot / "plugin/plugins/installed").exists()
    assert not (snapshot / "plugin/tests").exists()
    assert not (snapshot / "utils/__pycache__").exists()
    assert not (snapshot / "utils/large-asset.zip").exists()
    assert (snapshot / "sitecustomize.py").is_file()


def test_snapshot_must_not_be_created_inside_source_host(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    with pytest.raises(ValueError, match="outside"):
        isolation.copy_host_sources(host, host / "nested")


def test_write_guard_is_inherited_by_python_descendants(tmp_path):
    host = tmp_path / "source-host"
    host.mkdir()
    root = tmp_path / "sandbox"
    snapshot = root / "host"
    isolation.copy_host_sources(host, snapshot)
    outside = tmp_path / "must-not-exist.txt"
    inside = root / "allowed.txt"
    worker = (
        "import os,sys; from pathlib import Path; "
        "assert getattr(sys,'_sticker_isolation_root',None); "
        "open(os.devnull,'w').close(); "
        "Path(sys.argv[1]).write_text('allowed'); "
        "Path(sys.argv[2]).write_text('forbidden')"
    )
    code = (
        "import subprocess,sys; "
        f"p=subprocess.run([sys.executable,'-c',{worker!r},sys.argv[1],sys.argv[2]],capture_output=True,text=True); "
        "assert p.returncode != 0; "
        "assert 'isolated host refused filesystem write' in p.stderr"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(inside), str(outside)],
        cwd=snapshot, env=isolation.isolated_environment(snapshot, root),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert inside.read_text() == "allowed"
    assert not outside.exists()


def test_guard_allows_asyncio_self_pipe_but_rejects_host_connections(tmp_path):
    host = tmp_path / "source-host"
    host.mkdir()
    root = tmp_path / "sandbox"
    snapshot = root / "host"
    isolation.copy_host_sources(host, snapshot)
    code = (
        "import asyncio,socket\n"
        "asyncio.run(asyncio.sleep(0))\n"
        "try:\n"
        "    socket.create_connection(('127.0.0.1',48916),timeout=1)\n"
        "except PermissionError as exc:\n"
        "    assert 'isolated host refused network connection' in str(exc)\n"
        "else:\n"
        "    raise AssertionError('real host connection was permitted')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=snapshot,
        env=isolation.isolated_environment(snapshot, root),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
