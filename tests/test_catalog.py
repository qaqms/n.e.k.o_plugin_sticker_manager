"""core/catalog.py 的纯函数测试：格式嗅探、id、标签、检索、目录文案。"""

from __future__ import annotations

import time

from conftest import GIF_BYTES, JPEG_BYTES, NOT_AN_IMAGE, PNG_BYTES, WEBP_BYTES
from sticker_manager.core.catalog import (
    Sticker,
    detect_image_format,
    format_catalog_for_model,
    is_animated_gif,
    new_sticker_id,
    normalize_tags,
    parse_tags_field,
    search_stickers,
    validate_desc,
)


class TestDetectImageFormat:
    def test_png_jpg_gif_webp_recognized(self):
        assert detect_image_format(PNG_BYTES) == ("png", "image/png")
        assert detect_image_format(JPEG_BYTES) == ("jpg", "image/jpeg")
        assert detect_image_format(GIF_BYTES) == ("gif", "image/gif")
        assert detect_image_format(WEBP_BYTES) == ("webp", "image/webp")

    def test_non_image_rejected(self):
        assert detect_image_format(NOT_AN_IMAGE) is None
        assert detect_image_format(b"") is None

    def test_webp_requires_both_riff_and_webp_markers(self):
        # RIFF 头但非 WEBP 签名（如 AVI）必须被拒
        fake_avi = b"RIFF" + (70).to_bytes(4, "little") + b"AVI " + b"\x00" * 62
        assert detect_image_format(fake_avi) is None

    def test_extension_never_trumps_magic(self):
        # 嗅探只看文件头：给了 .png 名字的内容嗅探结果不变（入库口就是唯一判据）
        assert detect_image_format(NOT_AN_IMAGE) is None


class TestAnimatedGif:
    def test_single_frame_not_animated(self):
        assert is_animated_gif(GIF_BYTES) is False

    def test_netscape_loop_detected(self):
        data = GIF_BYTES[:6] + b"NETSCAPE2.0" + GIF_BYTES[17:]
        assert is_animated_gif(data) is True

    def test_non_gif_always_false(self):
        assert is_animated_gif(PNG_BYTES) is False


class TestIdsAndFields:
    def test_new_id_unique_against_existing(self):
        existing = {"a1b2c3d4e5"}
        sid = new_sticker_id(existing)
        assert len(sid) == 10
        assert sid not in existing

    def test_normalize_tags_dedupes_and_caps(self):
        tags = normalize_tags([" 开心 ", "开心", "KIRA", "x" * 40, 123, "cat", " "])
        assert tags == ["开心", "KIRA", "cat"]

    def test_normalize_tags_casefold_dedupe(self):
        assert normalize_tags(["Cat", "cat"]) == ["Cat"]

    def test_parse_tags_field_accepts_both_shapes(self):
        assert parse_tags_field("a，b、c,a") == ["a", "b", "c"]
        assert parse_tags_field(["a", "b"]) == ["a", "b"]
        assert parse_tags_field(None) == []
        assert parse_tags_field(42) == []

    def test_validate_desc(self):
        assert validate_desc(" 猫笑着挥手 ") == ("猫笑着挥手", "")
        assert validate_desc("") == ("", "empty")
        assert validate_desc("   ") == ("", "empty")
        assert validate_desc("x" * 201)[1] == "too_long"
        assert validate_desc(None) == ("", "empty")


class TestStickerRecord:
    def test_roundtrip(self):
        sticker = Sticker(id="abc", file="abc.png", desc="笑", tags=["开心"], added_at=1.0)
        restored = Sticker.from_dict(sticker.as_dict())
        assert restored == sticker

    def test_from_dict_drops_bad_shapes(self):
        assert Sticker.from_dict(None) is None
        assert Sticker.from_dict("x") is None
        assert Sticker.from_dict({"id": "", "file": "a.png"}) is None
        assert Sticker.from_dict({"id": "a", "file": ""}) is None
        # desc 缺失不炸：宽松还原成空串
        loose = Sticker.from_dict({"id": "a", "file": "a.png"})
        assert loose is not None and loose.desc == ""

    def test_with_touch_increments(self):
        sticker = Sticker(id="abc", file="abc.png", desc="笑", use_count=3, last_used_at=1.0)
        touched = sticker.with_touch(now=99.0)
        assert touched.use_count == 4
        assert touched.last_used_at == 99.0
        # 原对象不动（frozen dataclass 的复制语义）
        assert sticker.use_count == 3


def _s(sid: str, desc: str, tags=(), disabled=False, uses=0, last=0.0) -> Sticker:
    return Sticker(
        id=sid, file=f"{sid}.png", desc=desc, tags=list(tags),
        disabled=disabled, added_at=time.time(), use_count=uses, last_used_at=last,
    )


class TestSearch:
    def setup_method(self):
        self.pool = [
            _s("id001", "猫咪开心挥手", ["开心", "猫"], uses=5, last=100.0),
            _s("id002", "无语凝噎", ["无语"], uses=9, last=90.0),
            _s("id003", "开心到飞起", ["开心"], disabled=True, uses=1, last=80.0),
            _s("id004", "猫猫思考", ["猫"], uses=2, last=95.0),
        ]

    def test_empty_query_sorted_by_use_count_then_recency(self):
        # 排序键：use_count 降序（9,5,2），同次数再按最近使用
        result = search_stickers(self.pool, "")
        assert [s.id for s in result] == ["id002", "id001", "id004"]

    def test_disabled_excluded_by_default(self):
        ids = [s.id for s in search_stickers(self.pool, "开心")]
        assert "id003" not in ids
        assert "id001" in ids

    def test_include_disabled(self):
        ids = [s.id for s in search_stickers(self.pool, "开心", include_disabled=True)]
        assert "id003" in ids

    def test_exact_id_beats_desc_match(self):
        result = search_stickers(self.pool, "id002")
        assert result[0].id == "id002"

    def test_tag_match_ranks_below_desc_match(self):
        result = search_stickers(self.pool, "开心")
        assert [s.id for s in result][0] == "id001"  # desc 命中

    def test_case_insensitive_query(self):
        pool = [_s("id010", "Cool cat", ["CUTE"])]
        assert search_stickers(pool, "cute")[0].id == "id010"

    def test_no_match_returns_empty(self):
        assert search_stickers(self.pool, "不存在的词") == []


class TestCatalogForModel:
    def test_line_shape(self):
        pool = [_s("id001", "猫咪开心挥手", ["开心", "猫"])]
        text = format_catalog_for_model(pool, 10)
        assert text == "[id001] 猫咪开心挥手（标签：开心/猫）"

    def test_disabled_and_limit(self):
        pool = [_s(f"id{i}", f"表情{i}", disabled=(i == 1)) for i in range(5)]
        text = format_catalog_for_model(pool, 3)
        lines = text.splitlines()
        assert len(lines) == 3
        assert all("表情1" not in line for line in lines)

    def test_empty_library_is_empty_string(self):
        assert format_catalog_for_model([], 10) == ""
