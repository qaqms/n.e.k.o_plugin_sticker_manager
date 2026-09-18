# pyright: reportMissingImports=false
"""v0.14.0「配表情积极度」三档的门。

钉四件事：
1. **档位只管意愿**：注入文案的意愿段随档变，节奏段（发送层事实）三档共用；
2. **默认档 = 老行为**：natural 的意愿段与 v0.13.0 那句一字不差；
3. **读入收敛**：不在册/非字符串/缺键一律回 natural，配置写错不该让她的行为变野；
4. **入口与面板**：`set_eagerness` 写对配置路径、拒非法值、盘写不进去如实报错；
   dashboard context 露当前档（Select 回填靠它）。
"""

from __future__ import annotations

from conftest import FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.awareness import build_awareness_text
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import (
    EAGERNESS_LEVELS,
    SendSettings,
    StickerManagerSettings,
)


def _settings(tmp_path, *, send: dict | None = None, enabled=True):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": enabled, "send": send or {}}}),
    )
    plugin, host = build_plugin(host)
    plugin._settings = StickerManagerSettings.from_config(host.config.data)
    return plugin, host


def _stickers(count: int = 2):
    return [
        Sticker(id=f"s{i}", file=f"{i}.gif", desc=f"梗{i}", tags=[], added_at=float(i))
        for i in range(count)
    ]


def _text(eagerness: str):
    return build_awareness_text(_stickers(), max_lines=5, groups={}, eagerness=eagerness)


class TestReadIn:
    def test_default_tier_is_natural(self):
        assert SendSettings().eagerness == "natural"
        assert StickerManagerSettings.from_config({}).send.eagerness == "natural"

    def test_unknown_tier_falls_back_to_default(self):
        for junk in ("wild", "", "  ", 7, None, True, ["eager"]):
            got = StickerManagerSettings.from_config(
                {"sticker_manager": {"send": {"eagerness": junk}}}
            ).send.eagerness
            assert got == "natural", junk

    def test_surrounding_whitespace_is_trimmed(self):
        got = StickerManagerSettings.from_config(
            {"sticker_manager": {"send": {"eagerness": "  eager  "}}}
        ).send.eagerness
        assert got == "eager"


class TestInjectionText:
    def test_three_tiers_differ_in_will_but_share_the_rhythm(self):
        texts = {tier: _text(tier) for tier in EAGERNESS_LEVELS}
        assert len({text for text in texts.values()}) == 3
        for tier, text in texts.items():
            assert "别重试、也别换一张接着试" in text, f"{tier} 丢了发送层的事实"
            assert "sticker_send" in text

    def test_natural_keeps_the_old_sentence_verbatim(self):
        # 默认档不许偷偷改老行为——这句是 v0.13.0 起她就看到的措辞。
        assert "宁缺毋滥" in _text("natural") and "多数时候纯文字就够了" not in _text("natural")
        assert "多数时候纯文字就够了" in _text("reserved")
        assert "别在心里过三遍才发" in _text("eager")

    def test_unlisted_tier_degrades_to_natural(self):
        assert _text("wild") == _text("natural")

    def test_service_passes_the_configured_tier(self, tmp_path):
        plugin, _host = _settings(tmp_path, send={"eagerness": "reserved"})
        assert plugin._settings.send.eagerness == "reserved"


class TestEntry:
    def test_set_writes_config_path_and_returns_applied(self, tmp_path, run_async):
        plugin, host = _settings(tmp_path)
        result = run_async(plugin.set_eagerness_entry(eagerness="eager"))
        assert result.is_ok()
        assert result.value["note"] == "eagerness_set"
        assert result.value["eagerness"] == "eager"
        assert host.config.writes == [("sticker_manager.send.eagerness", "eager")]

    def test_invalid_tier_is_refused_not_defaulted(self, tmp_path, run_async):
        plugin, host = _settings(tmp_path)
        result = run_async(plugin.set_eagerness_entry(eagerness="wild"))
        assert not result.is_ok() and str(result.error) == "invalid_value"
        assert host.config.writes == []

    def test_config_failure_surfaces(self, tmp_path, run_async):
        plugin, host = _settings(tmp_path)
        host.config.set_error = RuntimeError("boom")
        result = run_async(plugin.set_eagerness_entry(eagerness="reserved"))
        assert not result.is_ok() and str(result.error) == "config_unavailable"

    def test_dashboard_exposes_current_tier(self, tmp_path, run_async):
        plugin, _host = _settings(tmp_path, send={"eagerness": "eager"})
        state = run_async(plugin.dashboard_context())
        assert state["eagerness"] == "eager"
