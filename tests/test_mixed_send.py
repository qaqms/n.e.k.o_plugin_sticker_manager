# pyright: reportMissingImports=false
"""v0.18.0「图文同条」的门：图和她那句话一起出去，或者一起不出去。

来由：主人选了参考那边 `enable_mixed` 的等价物。宿主侧我核过才敢做——
`parts=[{text},{image}]` 会经 `_ordered_plugin_chat_blocks(include_text=True)`
渲染成**同一个来源气泡**并按顺序保留（宿主 `app/main_server/character_runtime.py:1154,1380`），
但**署名一定是插件**：`render_chat_blocks` 明写插件内容"既不是助手也不是用户，
把它扮成任一方都是读者无法核实的谎"（同文件 1355-1372 与 `main_logic/core/turn.py:1804-1816`）。
曾经能以她身份上屏的 `passthrough_to_chat_bubble` 仍在树里，但注释写明
"NO PRODUCTION CALLER remains" 且这条规矩是被有意拆掉的。所以这里做的是什么都能做的那一半：
**她把话写进工具参数 → 一次调用 → 一条气泡**，动作从两段压成一段，署名如实。

钉得最狠的一条是"一起不出去"（`test_text_never_survives_a_lost_image`）：
只把话投出去会造出一句没人应答的裸文本，那比不发更糟——也是这条功能最容易做歪的地方。
"""

from __future__ import annotations

import json

from conftest import GIF_BYTES, PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.catalog import SEND_TEXT_MAX_CHARS
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings
from sticker_manager.core.tool_surface import build_send_tool_description

_SENTINEL = "哨兵：这句话绝不能落到台账里"


def _setup(tmp_path, *, send_overrides=None):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
    )
    plugin, host = build_plugin(host)
    settings = StickerManagerSettings(
        enabled=True,
        send=SendSettings(recent_dedup_count=0, **(send_overrides or {})),
        storage=StorageSettings(),
    )
    plugin._settings = settings
    plugin._library.load(force=True)
    return plugin, host, settings


class TestMixedParts:
    def test_text_and_image_share_one_push_in_order(self, tmp_path, run_async):
        plugin, host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text="就这？"
            )
        )
        assert result.ok
        (call,) = host.push.calls  # 只有一条 push：没有"图一条、话一条"
        parts = call["parts"]
        assert [p["type"] for p in parts] == ["text", "image"]
        assert parts[0]["text"] == "就这？"
        assert parts[1]["data"] == PNG_BYTES

    def test_without_text_the_old_shape_is_untouched(self, tmp_path, run_async):
        # 反向样本：默认路径被改动过（比如无条件塞一个空 text part）就该红——
        # 老形状是主人实机验收过 6 版的东西。
        plugin, host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        (call,) = host.push.calls
        assert [p["type"] for p in call["parts"]] == ["image"]
        assert call["visibility"] == ["chat"] and call["ai_behavior"] == "read"

    def test_sender_forwards_the_words_verbatim(self, tmp_path, run_async):
        # 归位：sender 拿到什么就投什么（不改写她的措辞），trim 是工具入口的事。
        plugin, host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text="  嗯  ")
        )
        assert host.push.calls[0]["parts"][0]["text"] == "  嗯  "

    def test_tool_trims_the_words_before_sending(self, tmp_path, run_async):
        plugin, host, _settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, text="  嗯  "))
        assert result.get("ok") is True, result
        assert host.push.calls[-1]["parts"][0]["text"] == "嗯"


class TestAllOrNothing:
    def test_text_never_survives_a_lost_image(self, tmp_path, run_async):
        # 图放不下（动图超预算）时，那句话也不许单独出去：裸投一句没人应答的话，
        # 比这条不发更糟。
        plugin, host, settings = _setup(tmp_path)
        big_gif = GIF_BYTES + b"\x00" * (300 * 1024)
        sticker, _ = plugin._library.add(data=big_gif, desc="大动图", tags=[])
        result = run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text="来看看这个"
            )
        )
        assert not result.ok and result.code == "sticker_too_large"
        assert host.push.calls == []

    def test_caption_counts_against_the_inline_budget(self, tmp_path, run_async):
        """内联尺量的是**整条载荷**，不是只看图字节。

        用动图测才对：静态图超预算会改走上传换 URL（那是另一条通道、另一个尺），
        只有 gif 是"内联不下就如实拒发"（陷阱 1：上传会把它压平成一帧 JPEG）。
        反向样本：`_build_part` 里忘了减 `text_len`，第二段就会拿到 ok=True。
        """
        plugin, host, settings = _setup(tmp_path, send_overrides={"inline_max_bytes": 200_000})
        gif = GIF_BYTES + b"\x00" * 190_000  # 单看字节放得下；加上一句话就放不下
        sticker, _ = plugin._library.add(data=gif, desc="紧巴巴", tags=[])
        tight = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert tight.ok and len(host.push.calls) == 1
        # 第二次要越过 20s 冷却，否则先撞的是节奏闸（那测的就不是预算尺了）。
        crowded = run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1099.0, text="x" * 20_000
            )
        )
        assert not crowded.ok and crowded.code == "sticker_too_large"
        assert len(host.push.calls) == 1, "超预算那条一次都不该投"
        assert host.images.calls == [], "gif 不许为了塞进去改走上传通道"


class TestToolSurfaceForText:
    def test_oversized_caption_is_refused_before_any_delivery(self, tmp_path, run_async):
        plugin, host, _settings = _setup(tmp_path)
        plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(plugin.tool_sticker_send(group="", sticker_id="", query="笑", text="啊" * (SEND_TEXT_MAX_CHARS + 1)))
        assert result.get("ok") is False and result.get("reason") == "text_too_long"
        assert host.push.calls == []

    def test_the_standing_surface_teaches_the_one_call_shape(self):
        # 常驻面上要有这句（她得知道"一次调用就能把话说完"），但它不许变成新的禁令。
        text = build_send_tool_description("・困与睡（9 张） — 深夜用。", "natural")
        assert "text" in text and "一起出去" in text
        for banned in ("会拒", "别连试", "只有主人点名", "force", "挡下", "最近不重复", "冷却"):
            assert banned not in text, f"图文同条的描述里混进了禁令词：{banned}"


class TestLedgerHonesty:
    def test_usage_records_length_not_the_words(self, tmp_path, run_async):
        # 台账既有纪律是"只放非隐私字段"。这条就是那把尺在新字段上的反向验。
        plugin, host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text=_SENTINEL
            )
        )
        raw = (plugin._library.root / "usage.json").read_text(encoding="utf-8")
        assert _SENTINEL not in raw, "配文内容落盘了"
        entry = json.loads(raw)["entries"][-1]
        assert entry["text_len"] == len(_SENTINEL) and entry["ok"] is True

    def test_plain_send_records_zero_length(self, tmp_path, run_async):
        plugin, _host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        entry = json.loads((plugin._library.root / "usage.json").read_text(encoding="utf-8"))["entries"][-1]
        assert entry["text_len"] == 0

    def test_log_line_shows_the_length_not_the_sentence(self, tmp_path, run_async):
        recorder: list[str] = []

        class _Log:
            def info(self, message, **_k):
                recorder.append(str(message))

            def warning(self, message, **_k):
                recorder.append(str(message))

            def error(self, message, **_k):
                recorder.append(str(message))

            def debug(self, message, **_k):
                recorder.append(str(message))

        plugin, _host, settings = _setup(tmp_path)
        plugin._sender._logger = _Log()
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text=_SENTINEL
            )
        )
        line = [x for x in recorder if "sticker sent" in x]
        assert line and f"text_len={len(_SENTINEL)}" in line[0]
        assert _SENTINEL not in "\n".join(recorder)
