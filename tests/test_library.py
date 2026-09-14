"""services/library.py 的持久层测试（tmp_path 文件系统，真写盘）。"""

from __future__ import annotations

import json

from conftest import GIF_BYTES, JPEG_BYTES, NOT_AN_IMAGE, PNG_BYTES
from sticker_manager.services.library import ERR_NOT_FOUND, Library


def _library(root) -> Library:
    lib = Library(root)
    assert lib.load().ok
    return lib


class TestAddAndLoad:
    def test_add_writes_file_and_catalog(self, tmp_path):
        lib = _library(tmp_path)
        sticker, error = lib.add(data=PNG_BYTES, desc="笑", tags=["开心"], now=100.0)
        assert error == "" and sticker is not None
        # 文件真实落盘，扩展名由魔数决定
        path = lib.image_path(sticker)
        assert path.is_file()
        assert path.read_bytes() == PNG_BYTES
        assert path.name == f"{sticker.id}.png"
        # 重载后条目还在
        lib2 = _library(tmp_path)
        assert lib2.get(sticker.id) is not None
        assert lib2.get(sticker.id).desc == "笑"

    def test_add_rejects_non_image(self, tmp_path):
        lib = _library(tmp_path)
        sticker, error = lib.add(data=NOT_AN_IMAGE, desc="坏", tags=[])
        assert sticker is None
        assert error == "invalid_image"
        assert lib.count() == 0

    def test_extension_follows_magic_not_request(self, tmp_path):
        # 入库口只认文件头：喂 jpg 字节就是 .jpg，没有 filename 参数可撒谎
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=JPEG_BYTES, desc="照片", tags=[])
        assert sticker.file.endswith(".jpg")

    def test_gif_keeps_gif_extension(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=GIF_BYTES, desc="动图", tags=[])
        assert sticker.file.endswith(".gif")

    def test_catalog_survives_bad_entries(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="好", tags=[])
        # 手写坏条目进 catalog.json：好条目必须还在（宽松丢弃，不炸整本）
        raw = json.loads(lib.catalog_path.read_text(encoding="utf-8"))
        raw["stickers"].append({"nope": True})
        raw["stickers"].append({"id": "bad", "file": 42})
        lib.catalog_path.write_text(json.dumps(raw), encoding="utf-8")
        lib2 = _library(tmp_path)
        assert lib2.get(sticker.id) is not None
        assert lib2.get("bad") is None

    def test_unreadable_catalog_reports_io_code(self, tmp_path):
        (tmp_path).mkdir(parents=True, exist_ok=True)
        (tmp_path / "catalog.json").write_text("{ not json", encoding="utf-8")
        lib = Library(tmp_path)
        result = lib.load()
        assert not result.ok
        assert result.code == "library_io_error"
        assert lib.io_dirty is True


class TestMutations:
    def test_update_desc_and_disable(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="旧", tags=["a"], now=1.0)
        updated, error = lib.update(sticker.id, desc="新", disabled=True)
        assert error == ""
        assert updated.desc == "新"
        assert updated.disabled is True
        assert updated.tags == ["a"]
        # 重载确认落盘
        lib2 = _library(tmp_path)
        assert lib2.get(sticker.id).desc == "新"
        assert lib2.get(sticker.id).disabled is True

    def test_update_missing_returns_not_found(self, tmp_path):
        lib = _library(tmp_path)
        _updated, error = lib.update("nope", desc="x")
        assert error == ERR_NOT_FOUND

    def test_remove_deletes_entry_and_file(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="删我", tags=[])
        assert lib.remove(sticker.id) == ""
        assert lib.get(sticker.id) is None
        assert not lib.image_path(sticker).exists()
        lib2 = _library(tmp_path)
        assert lib2.get(sticker.id) is None

    def test_remove_missing(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.remove("nope") == ERR_NOT_FOUND

    def test_touch_used_persists(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="用", tags=[])
        lib.touch_used(sticker.id, now=55.0)
        lib2 = _library(tmp_path)
        reloaded = lib2.get(sticker.id)
        assert reloaded.use_count == 1
        assert reloaded.last_used_at == 55.0


class TestUsageLedger:
    def test_append_and_read_newest_first(self, tmp_path):
        lib = _library(tmp_path)
        lib.append_usage({"at": 1.0, "id": "a", "ok": True}, keep=100)
        lib.append_usage({"at": 2.0, "id": "b", "ok": False}, keep=100)
        entries = lib.read_usage(limit=10)
        assert [e["id"] for e in entries] == ["b", "a"]

    def test_keep_trims_oldest(self, tmp_path):
        lib = _library(tmp_path)
        for i in range(30):
            lib.append_usage({"at": float(i), "id": f"s{i}", "ok": True}, keep=20)
        entries = lib.read_usage(limit=100)
        assert len(entries) == 20
        assert entries[0]["id"] == "s29"
        assert entries[-1]["id"] == "s10"

    def test_missing_usage_file_is_empty(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.read_usage() == []


class TestDeduplication:
    def test_same_bytes_rejected_as_duplicate(self, tmp_path):
        lib = _library(tmp_path)
        first, error = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        assert error == "" and first.sha256  # 新入库即带内容指纹
        second, error = lib.add(data=PNG_BYTES, desc="又笑", tags=[])
        assert second is None and error == "duplicate_image"
        # 被拒的图不许留下孤儿文件
        files = list(lib.stickers_dir.iterdir())
        assert [f.name for f in files] == [first.file]

    def test_different_bytes_still_accepted(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.add(data=PNG_BYTES, desc="甲", tags=[])[1] == ""
        assert lib.add(data=JPEG_BYTES, desc="乙", tags=[])[1] == ""

    def test_legacy_entry_without_hash_detected_and_backfilled(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="旧", tags=[])
        # 模拟 v0.1.1 之前的旧数据：catalog 里没有 sha256 键
        raw = json.loads(lib.catalog_path.read_text(encoding="utf-8"))
        for entry in raw["stickers"]:
            entry.pop("sha256", None)
        lib.catalog_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        legacy = _library(tmp_path)
        assert legacy.get(sticker.id).sha256 == ""
        dup, error = legacy.add(data=PNG_BYTES, desc="重", tags=[])
        assert dup is None and error == "duplicate_image"
        # 查重时顺手回填：重载后旧条目带上指纹
        after = _library(tmp_path)
        assert after.get(sticker.id).sha256 != ""


class TestRepair:
    def test_removes_entries_with_missing_files_and_orphans(self, tmp_path):
        lib = _library(tmp_path)
        keep, _ = lib.add(data=PNG_BYTES, desc="留", tags=[])
        gone, _ = lib.add(data=JPEG_BYTES, desc="图没了", tags=[])
        lib.image_path(gone).unlink()
        orphan = lib.stickers_dir / "deadbeef00.png"
        orphan.write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 32)
        counts = lib.repair()
        assert counts["removed_entries"] == 1
        assert counts["purged_files"] == 1
        assert counts["backfilled_hashes"] == 0
        assert lib.get(keep.id) is not None and lib.get(gone.id) is None
        assert not orphan.exists()
        after = _library(tmp_path)
        assert after.get(gone.id) is None

    def test_backfills_hashes_of_legacy_entries(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="旧", tags=[])
        raw = json.loads(lib.catalog_path.read_text(encoding="utf-8"))
        for entry in raw["stickers"]:
            entry.pop("sha256", None)
        lib.catalog_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        legacy = _library(tmp_path)
        counts = legacy.repair()
        assert counts["backfilled_hashes"] == 1
        assert counts["removed_entries"] == 0
        assert legacy.get(sticker.id).sha256 != ""

    def test_repair_on_empty_library_is_clean(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.repair() == {"removed_entries": 0, "purged_files": 0, "backfilled_hashes": 0}
