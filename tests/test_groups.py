# pyright: reportMissingImports=false
# 独立仓无 sticker_manager 目录名，测试态由 conftest 的 importlib 别名接管（pytest 实测可解），同 __init__.py 的 SDK 导入先例
"""轮 F（v0.7.0 轻打标）：分组描述、两级目录、组内选图、批量整理、manifest v3。

对齐参考系统的手感：描述挂分类（一句话即目录正文）、逐图零必填、
"组调性对、具体哪张你定"→组内随机；整理靠批量而不是逐张开弹窗。
"""

from __future__ import annotations

import base64
import io
import json
import zipfile

import sticker_manager as pkg
from conftest import PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.catalog import Sticker, format_group_overview
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings
from sticker_manager.services.library import Library


def _ctx(lanlan: str) -> dict:
    return {"_ctx": {"lanlan_name": lanlan}}


def _plugin(tmp_path, *, send: SendSettings | None = None):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
    )
    plugin, _host = build_plugin(host)
    plugin._settings = StickerManagerSettings(
        enabled=True, send=send or SendSettings(), storage=StorageSettings()
    )
    return plugin


async def _add(plugin, desc="", tags="", group="", caption=""):
    # 字节里拼入字段内容：不同参数组合必不撞 sha256（只拼长度会同长碰撞 duplicate_image）
    payload = PNG_BYTES + f"|{desc}|{tags}|{group}|{caption}".encode("utf-8")
    return await plugin.add_entry(
        data_base64=base64.b64encode(payload).decode("ascii"),
        desc=desc,
        tags=tags,
        group=group,
        caption=caption,
    )


# ----------------------------------------------------------------------
# core：catalog_body 回落链
# ----------------------------------------------------------------------


class TestCatalogBodyChain:
    def test_precedence(self):
        base = dict(id="a", file="a.png")
        assert Sticker(**base, desc="", caption="梗").catalog_body() == "梗"
        assert Sticker(**base, desc="述", caption="").catalog_body({"G": "组话"}) == "述"
        assert Sticker(**base, desc="", caption="", group="G").catalog_body({"G": "组话"}) == "组话"
        assert Sticker(**base, desc="", caption="", group="G").catalog_body({}) == "套图「G」里的一张"
        assert Sticker(**base, desc="", caption="").catalog_body() == "未标注"

    def test_group_overview_shape(self):
        a = Sticker(id="a", file="a.png", desc="甲", group="常用")
        b = Sticker(id="b", file="b.png", desc="乙", group="常用")
        c = Sticker(id="c", file="c.png", desc="丙")
        d = Sticker(id="d", file="d.png", desc="丁", group="禁用组", disabled=True)
        text = format_group_overview([a, b, c, d], {"常用": "开心时", "禁用组": "别看"})
        assert text.splitlines() == ["・常用（2 张） — 开心时", "・未分组（1 张）"]


# ----------------------------------------------------------------------
# library：分组说明持久
# ----------------------------------------------------------------------


class TestLibraryGroups:
    def test_set_persist_clear(self, tmp_path):
        lib = Library(tmp_path / "lib")
        lib.load()
        ok, err = lib.set_group_desc(" 猫猫日常 ", " 被夸时用 ")
        assert ok and not err
        assert lib.group_descs() == {"猫猫日常": "被夸时用"}
        reloaded = Library(tmp_path / "lib")
        reloaded.load()
        assert reloaded.group_descs() == {"猫猫日常": "被夸时用"}
        ok, _ = lib.set_group_desc("猫猫日常", "")
        assert ok
        assert lib.group_descs() == {}

    def test_clamps_and_guards(self, tmp_path):
        lib = Library(tmp_path / "lib")
        lib.load()
        assert lib.set_group_desc("", "没名字") == (False, "group_required")
        ok, _ = lib.set_group_desc("G", "x" * 400)
        assert ok and len(lib.group_descs()["G"]) == 300  # 入口层拦，库层钳位兜底

    def test_poisoned_groups_section(self, tmp_path):
        root = tmp_path / "lib"
        root.mkdir(parents=True)
        (root / "catalog.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "stickers": [],
                    "groups": {"好组": "ok", "空组": "   ", "坏值": 42, "": "无名"},
                }
            ),
            encoding="utf-8",
        )
        lib = Library(root)
        result = lib.load(force=True)
        assert result.ok
        assert lib.group_descs() == {"好组": "ok"}


# ----------------------------------------------------------------------
# 入口面
# ----------------------------------------------------------------------


class TestGroupDescEntry:
    def test_roundtrip_and_dashboard(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        added = run_async(_add(plugin, desc="图一", group="猫猫日常"))
        assert added.is_ok()
        result = run_async(plugin.group_set_desc_entry(group="猫猫日常", desc="被夸/撒娇时用"))
        assert result.is_ok()
        assert result.value["note"] == "group_desc_set"
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert payload["groups"] == [{"name": "猫猫日常", "count": 1, "desc": "被夸/撒娇时用"}]
        # 空串=清除，但组还在（有图在用）
        cleared = run_async(plugin.group_set_desc_entry(group="猫猫日常", desc="  "))
        assert cleared.is_ok() and cleared.value["cleared"] is True

    def test_guards(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        assert str(run_async(plugin.group_set_desc_entry()).error) == "group_required"
        assert str(run_async(plugin.group_set_desc_entry(group="不存在", desc="x")).error) == "group_not_found"
        run_async(_add(plugin, desc="图", group="G"))
        too_long = "x" * 301
        assert str(run_async(plugin.group_set_desc_entry(group="G", desc=too_long)).error) == "group_desc_too_long"


class TestBatchEntries:
    def _ids(self, plugin, run_async):
        listed = run_async(plugin.list_entry())
        return [row["id"] for row in listed.value["stickers"]]

    def test_batch_update_ops(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        for i in range(3):
            run_async(_add(plugin, desc=f"图{i}", tags="旧"))
        ids = self._ids(plugin, run_async)
        assert run_async(plugin.batch_update_entry(ids=ids, tags_add="甲,乙")).value["updated"] == 3
        listed = run_async(plugin.list_entry())
        for row in listed.value["stickers"]:
            assert set(["旧", "甲", "乙"]) <= set(row["tags"])
        assert run_async(plugin.batch_update_entry(ids=ids, tags_remove="旧")).value["updated"] == 3
        listed = run_async(plugin.list_entry())
        assert all("旧" not in row["tags"] for row in listed.value["stickers"])
        assert run_async(plugin.batch_update_entry(ids=ids, group="一批", disabled=True)).value["updated"] == 3
        listed = run_async(plugin.list_entry(include_disabled=True))
        assert all(row["group"] == "一批" and row["disabled"] for row in listed.value["stickers"])

    def test_batch_guards(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        assert str(run_async(plugin.batch_update_entry(ids=[])).error) == "batch_empty"
        assert str(run_async(plugin.batch_update_entry(ids=["a"])).error) == "batch_noop"
        result = run_async(plugin.batch_update_entry(ids=["幽灵"], tags_add="x"))
        assert result.is_ok()
        assert result.value["updated"] == 0 and result.value["missing"] == ["幽灵"]
        assert str(run_async(plugin.batch_remove_entry(ids=[])).error) == "batch_empty"

    def test_batch_remove(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        run_async(_add(plugin, desc="留"))
        run_async(_add(plugin, desc="删A"))
        run_async(_add(plugin, desc="删B"))
        ids = self._ids(plugin, run_async)
        result = run_async(plugin.batch_remove_entry(ids=[ids[0], ids[1], "幽灵"]))
        assert result.value["removed"] == 2 and result.value["missing"] == ["幽灵"]
        assert run_async(plugin.list_entry()).value["count"] == 1


# ----------------------------------------------------------------------
# 工具面：组内选图与两级目录
# ----------------------------------------------------------------------


class TestSendGroup:
    def _fill(self, plugin, run_async):
        run_async(_add(plugin, desc="甲", group="happy组"))
        run_async(_add(plugin, desc="乙", group="happy组"))

    def test_exact_group_picks_within(self, tmp_path, run_async, monkeypatch):
        plugin = _plugin(tmp_path, send=SendSettings(cooldown_sec=0.0, recent_dedup_count=0))
        self._fill(plugin, run_async)
        monkeypatch.setattr(pkg, "_GROUP_PICK", lambda seq: seq[-1])  # 钉死"选池子里最后一张"
        out = run_async(plugin.tool_sticker_send(group="happy组", **_ctx("K")))
        assert out["ok"] and out["sent"]
        listed = run_async(plugin.list_entry())
        group_ids = {r["id"] for r in listed.value["stickers"] if r["group"] == "happy组"}
        assert out["sent"] in group_ids and len(group_ids) == 2
        # 选中的那张被计数，另一张没被动过——"随机"只随机在选谁，不篡改台账
        touched = [r for r in listed.value["stickers"] if r["id"] == out["sent"]][0]
        assert touched["use_count"] == 1

    def test_recent_excluded_then_repeat(self, tmp_path, run_async, monkeypatch):
        plugin = _plugin(tmp_path, send=SendSettings(cooldown_sec=0.0, recent_dedup_count=5))
        self._fill(plugin, run_async)
        picks = []
        monkeypatch.setattr(pkg, "_GROUP_PICK", lambda seq: (picks.append(list(seq)), seq[0])[1])
        first = run_async(plugin.tool_sticker_send(group="happy组", **_ctx("K")))
        assert first["ok"]
        second = run_async(plugin.tool_sticker_send(group="happy组", **_ctx("K")))
        assert second["ok"] and second["sent"] != first["sent"]
        assert len(picks[1]) == 1  # 第二刀的池子里只剩一张：刚发的被去重尺剔除
        third = run_async(plugin.tool_sticker_send(group="happy组", **_ctx("K")))
        assert third["ok"] is False and third["reason"] == "recent_repeat"
        forced = run_async(plugin.tool_sticker_send(group="happy组", force=True, **_ctx("K")))
        assert forced["ok"] is True  # force 把整组池子放回来

    def test_group_errors_are_honest(self, tmp_path, run_async):
        plugin = _plugin(tmp_path, send=SendSettings(cooldown_sec=0.0))
        run_async(_add(plugin, desc="甲", group="猫猫日常A"))
        run_async(_add(plugin, desc="乙", group="猫猫日常B"))
        missing = run_async(plugin.tool_sticker_send(group="没有这组", **_ctx("K")))
        assert missing["ok"] is False and missing["reason"] == "group_not_found"
        assert "猫猫日常A" in missing["hint"]
        ambiguous = run_async(plugin.tool_sticker_send(group="猫猫日常", **_ctx("K")))
        assert ambiguous["ok"] is True and ambiguous["note"] == "group_candidates"
        assert "猫猫日常A" in ambiguous["candidates"]

    def test_id_outranks_group(self, tmp_path, run_async):
        plugin = _plugin(tmp_path, send=SendSettings(cooldown_sec=0.0))
        run_async(_add(plugin, desc="甲", group="G"))
        other = run_async(_add(plugin, desc="乙", group="H"))
        target_id = other.value["id"]
        out = run_async(plugin.tool_sticker_send(sticker_id=target_id, group="G", **_ctx("K")))
        assert out["ok"] and out["sent"] == target_id


class TestListTwoTier:
    def test_no_query_shows_overview_first(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        run_async(_add(plugin, desc="图一", group="常用"))
        run_async(plugin.group_set_desc_entry(group="常用", desc="开心时"))
        out = run_async(plugin.tool_sticker_list())
        assert out["ok"] and out["catalog"].startswith("【套图分类】")
        assert "・常用（1 张） — 开心时" in out["catalog"] and "【条目】" in out["catalog"]

    def test_group_scoped_listing(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        run_async(_add(plugin, desc="甲", group="A组"))
        run_async(_add(plugin, desc="乙", group="B组"))
        out = run_async(plugin.tool_sticker_list(group="A组"))
        assert out["ok"] and "甲" in out["catalog"] and "乙" not in out["catalog"]
        none = run_async(plugin.tool_sticker_list(group="不存在"))
        assert none["ok"] and none["count"] == 0


# ----------------------------------------------------------------------
# manifest v3：分组说明随包迁移（只补缺不覆盖）
# ----------------------------------------------------------------------


class TestManifestV3Groups:
    def test_export_import_roundtrip(self, tmp_path, run_async):
        src = _plugin(tmp_path / "src")
        run_async(_add(src, desc="图一", group="猫猫日常"))
        run_async(src.group_set_desc_entry(group="猫猫日常", desc="被夸时用"))
        packed = run_async(src.export_pack_entry())
        assert packed.is_ok()
        zip_bytes = open(packed.value["file"], "rb").read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["version"] == 3
        assert manifest["groups"] == [{"name": "猫猫日常", "desc": "被夸时用"}]

        dst_root = tmp_path / "dst"
        dst = _plugin(dst_root)
        inbox = dst._library.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "pack.zip").write_bytes(zip_bytes)
        ingested = run_async(dst.import_inbox_entry())
        assert ingested.is_ok() and ingested.value["imported"] == 1
        assert dst._library.group_descs() == {"猫猫日常": "被夸时用"}
        # 只补缺不覆盖：本地已有组话时，包里的不能消音
        run_async(dst.group_set_desc_entry(group="猫猫日常", desc="我自己的写法"))
        (inbox / "pack2.zip").write_bytes(zip_bytes)
        run_async(dst.import_inbox_entry())
        assert dst._library.group_descs() == {"猫猫日常": "我自己的写法"}
