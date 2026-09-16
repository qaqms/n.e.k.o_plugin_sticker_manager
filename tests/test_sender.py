"""services/sender.py 的链路测试：通道选择、冷却、fail-closed、台账。"""

from __future__ import annotations

from conftest import GIF_BYTES, PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.configuration import (  # pyright: ignore[reportMissingImports] — 包名由 conftest 在测试时注册；独立仓静态面不可解析
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)


def _setup(tmp_path, *, enabled=True, send_overrides=None):
    host = FakeHostContext(data_root=tmp_path)
    plugin, host = build_plugin(host)
    # 节奏门（去重/概率）另有 test_rhythm.py 专铉；本文件只铉通道/冷却/fail-closed，
    # 默认把去重关掉免得误伤跨冷却窗口的重发场景。
    overrides = {"recent_dedup_count": 0, **(send_overrides or {})}
    settings = StickerManagerSettings(
        enabled=enabled,
        send=SendSettings(**overrides),
        storage=StorageSettings(),
    )
    lib = plugin._library
    lib.load(force=True)
    return plugin, host, settings, lib


class TestSendChannel:
    def test_small_image_goes_inline(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert result.ok
        (call,) = host.push.calls
        part = call["parts"][0]
        assert part["type"] == "image" and part["data"] == PNG_BYTES
        assert not host.images.calls  # 没走上传

    def test_large_image_goes_upload(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        big = PNG_BYTES + b"\x00" * (300 * 1024)  # 超过 256KiB 内联预算
        sticker, _ = lib.add(data=big, desc="大图", tags=[])
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert result.ok
        (call,) = host.push.calls
        part = call["parts"][0]
        assert part.get("url", "").startswith("https://stub.local/")
        assert len(host.images.calls) == 1  # 走过一次上传

    def test_gif_over_budget_refused_not_flattened(self, tmp_path, run_async):
        # 动图超过内联预算：宁可不发，也不走上传被压平成 JPEG
        plugin, host, settings, lib = _setup(tmp_path)
        big_gif = GIF_BYTES + b"\x00" * (300 * 1024)
        sticker, _ = lib.add(data=big_gif, desc="大动图", tags=[])
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "sticker_too_large"
        assert host.push.calls == []
        assert host.images.calls == []  # 上传通道一次都没碰

    def test_push_contract(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        (call,) = host.push.calls
        assert call["visibility"] == ["chat"]
        assert call["ai_behavior"] == "read"
        assert call["target_lanlan"] == "K"


class TestGuards:
    def test_fail_closed_when_disabled(self, tmp_path, run_async):
        from dataclasses import replace

        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        settings = replace(settings, enabled=False)
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "not_enabled"
        assert host.push.calls == []

    def test_disabled_sticker_never_leaks(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        banned, _ = lib.update(sticker.id, disabled=True)
        result = run_async(plugin._sender.send(banned, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "sticker_disabled"
        assert host.push.calls == []

    def test_missing_file_reports(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        lib.image_path(sticker).unlink()
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "sticker_file_missing"

    def test_transport_rejection_propagates(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        host.push.reject_reason = "payload_too_large"
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "payload_too_large"
        # 被拒的投递不推进冷却、不记使用次数
        assert plugin._sender.cooldown_remaining("K", settings, now=1000.0) == 0.0
        assert lib.get(sticker.id).use_count == 0

    def test_upload_failure_becomes_too_large(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        big = PNG_BYTES + b"\x00" * (300 * 1024)
        sticker, _ = lib.add(data=big, desc="大图", tags=[])
        host.images.fail = True
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "sticker_too_large"
        assert host.push.calls == []


class TestCooldown:
    def test_second_send_within_cooldown_blocked(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        first = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert first.ok
        second = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1010.0))
        assert not second.ok
        assert second.code == "send_cooldown"
        # 过了冷却窗口就放行
        third = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0 + 21.0))
        assert third.ok
        assert len(host.push.calls) == 2

    def test_cooldown_is_per_character(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        assert run_async(plugin._sender.send(sticker, lanlan="A", settings=settings, source="tool", now=1000.0)).ok
        assert run_async(plugin._sender.send(sticker, lanlan="B", settings=settings, source="tool", now=1001.0)).ok


class TestLedger:
    def test_success_recorded(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        entries = lib.read_usage()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["id"] == sticker.id
        assert entry["lanlan"] == "K"
        assert entry["source"] == "tool"
        assert entry["ok"] is True

    def test_failure_recorded_via_note_attempt(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        plugin._sender.note_attempt_failed(
            lanlan="K", sticker_id="ghost", code="no_match", settings=settings, now=1000.0
        )
        (entry,) = lib.read_usage()
        assert entry["ok"] is False
        assert entry["code"] == "no_match"
