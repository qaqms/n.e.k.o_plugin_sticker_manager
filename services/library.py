"""表情库的持久层：`data/catalog.json` + `data/stickers/<id>.<ext>` + `data/usage.json`。

为什么用 JSON 文件而不是 PluginStore 键值：
1. 图片本体就是文件，目录用 JSON 与它同根（`data_path`），备份/排障时一眼看全；
2. 避开"[plugin.store].enabled=false 时静默失效"的坑（our_life 陷阱 §4）——
   文件通道的失败是**响亮的**（IOError 会被捕获并转成稳定错误码）。

线程模型：所有写操作都来自入口/工具/面板调用（插件子进程的单一事件循环），
没有跨进程共享；load 是全量读 + 内存缓存，坏条目宽松丢弃、不炸整本目录。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.catalog import Sticker, content_sha256, detect_image_format, new_sticker_id

CATALOG_VERSION = 1
CATALOG_FILENAME = "catalog.json"
USAGE_FILENAME = "usage.json"
STICKER_DIRNAME = "stickers"

# 稳定错误码（面板与模型各自翻译/理解，见 DESIGN.md 的错误码契约）
ERR_IO = "library_io_error"
ERR_NOT_FOUND = "sticker_not_found"
ERR_EMPTY = "library_empty"
ERR_DUPLICATE = "duplicate_image"


@dataclass
class LibraryResult:
    ok: bool
    code: str = ""
    detail: str = ""

    @classmethod
    def failure(cls, code: str, detail: str = "") -> "LibraryResult":
        return cls(ok=False, code=code, detail=detail)


class Library:
    """表情库。`root` 是插件 data 目录（生产由 `plugin.data_path()` 提供）。"""

    def __init__(self, root: Path, *, logger: Any = None):
        self._root = Path(root)
        self._logger = logger
        self._stickers: dict[str, Sticker] = {}
        self._loaded = False
        self._io_dirty = False

    # ------------------------------------------------------------------
    # 路径与加载
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def stickers_dir(self) -> Path:
        return self._root / STICKER_DIRNAME

    @property
    def catalog_path(self) -> Path:
        return self._root / CATALOG_FILENAME

    def _log(self, message: str) -> None:
        # logger 缺席（测试直接构造 Library）时安静。
        if self._logger is not None:
            try:
                self._logger.info(message)
            except Exception:
                pass

    def load(self, *, force: bool = False) -> LibraryResult:
        """全量读目录。坏 JSON / 坏条目宽松处理：能救多少救多少。"""
        if self._loaded and not force:
            return LibraryResult(ok=True)
        self._stickers = {}
        try:
            raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._loaded = True
            self._io_dirty = False
            return LibraryResult(ok=True)
        except Exception:
            self._loaded = True
            self._io_dirty = True
            return LibraryResult.failure(ERR_IO, "catalog_unreadable")
        entries = raw.get("stickers") if isinstance(raw, dict) else None
        if isinstance(entries, list):
            for item in entries:
                sticker = Sticker.from_dict(item)
                if sticker is not None:
                    self._stickers[sticker.id] = sticker
        self._loaded = True
        self._io_dirty = False
        return LibraryResult(ok=True)

    def save(self) -> LibraryResult:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": CATALOG_VERSION,
                "updated_at": time.time(),
                "stickers": [s.as_dict() for s in self._stickers.values()],
            }
            tmp = self.catalog_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.catalog_path)
        except Exception:
            self._io_dirty = True
            return LibraryResult.failure(ERR_IO, "catalog_write_failed")
        self._io_dirty = False
        return LibraryResult(ok=True)

    @property
    def io_dirty(self) -> bool:
        """上一次读/写是否走过 IO 异常。面板横幅用它。"""
        return self._io_dirty

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def all(self) -> list[Sticker]:
        return sorted(
            self._stickers.values(),
            key=lambda s: (-s.added_at),
        )

    def get(self, sticker_id: str) -> Sticker | None:
        return self._stickers.get(sticker_id)

    def count(self) -> int:
        return len(self._stickers)

    def image_path(self, sticker: Sticker) -> Path:
        # file 字段只在本类内部生成（<id>.<ext>），不接受外部输入拼接，
        # 所以这里不需要再做路径逃逸检查——检查在 add() 的入库口。
        return self.stickers_dir / sticker.file

    # ------------------------------------------------------------------
    # 写路径
    # ------------------------------------------------------------------

    def _find_duplicate(self, digest: str) -> Sticker | None:
        """按内容指纹找同图。旧条目（v0.1.1 前入库、无指纹）现算并回填——
        回填是顺手的、幂等的；算不动（文件坏了）则跳过该条目，不阻断查重。
        """
        backfilled = False
        hit: Sticker | None = None
        for sticker in list(self._stickers.values()):
            sha = sticker.sha256
            if not sha:
                try:
                    sha = content_sha256(self.image_path(sticker).read_bytes())
                except Exception:
                    continue
                self._stickers[sticker.id] = sticker.with_sha256(sha)
                backfilled = True
            if sha == digest and hit is None:
                hit = sticker
        if backfilled:
            self.save()  # 回填失败不阻断入库（下次 repair/查重会再试）
        return hit

    def add(
        self,
        *,
        data: bytes,
        desc: str,
        tags: list[str],
        now: float | None = None,
    ) -> tuple[Sticker | None, str]:
        """入库一张图。返回 (条目, 错误码)；成功时错误码为空。

        错误码：invalid_image（不是受支持的图片格式）/ duplicate_image（库里已有同图）/ io_error。
        查重只认内容指纹，不认文件名（见 core/catalog 设计决定 5）。
        """
        detected = detect_image_format(data or b"")
        if detected is None:
            return None, "invalid_image"
        digest = content_sha256(data)
        if self._find_duplicate(digest) is not None:
            return None, ERR_DUPLICATE
        moment = time.time() if now is None else now
        try:
            sticker_id = new_sticker_id(self._stickers.keys())
        except RuntimeError:
            return None, ERR_IO
        ext, _mime = detected
        file_name = f"{sticker_id}.{ext}"
        try:
            self.stickers_dir.mkdir(parents=True, exist_ok=True)
            target = self.stickers_dir / file_name
            if target.exists():  # 16 次重采样仍撞名（几乎不可能）：换个 id 再来一次
                sticker_id = new_sticker_id({*self._stickers.keys(), file_name.split(".")[0]})
                file_name = f"{sticker_id}.{ext}"
                target = self.stickers_dir / file_name
            target.write_bytes(data)
        except Exception:
            return None, ERR_IO
        sticker = Sticker(
            id=sticker_id,
            file=file_name,
            desc=desc,
            tags=list(tags),
            added_at=moment,
            sha256=digest,
        )
        self._stickers[sticker_id] = sticker
        saved = self.save()
        if not saved.ok:
            self._stickers.pop(sticker_id, None)
            return None, saved.code
        self._log(f"sticker added: id={sticker_id} bytes={len(data)}")
        return sticker, ""

    def update(
        self,
        sticker_id: str,
        *,
        desc: str | None = None,
        tags: list[str] | None = None,
        disabled: bool | None = None,
    ) -> tuple[Sticker | None, str]:
        """改描述/标签/禁用态；None = 不改那一项（描述合法性由入口层把关）。"""
        sticker = self._stickers.get(sticker_id)
        if sticker is None:
            return None, ERR_NOT_FOUND
        updated = Sticker(
            id=sticker.id,
            file=sticker.file,
            desc=sticker.desc if desc is None else desc,
            tags=list(sticker.tags) if tags is None else list(tags),
            disabled=sticker.disabled if disabled is None else bool(disabled),
            added_at=sticker.added_at,
            use_count=sticker.use_count,
            last_used_at=sticker.last_used_at,
        )
        self._stickers[sticker_id] = updated
        saved = self.save()
        if not saved.ok:
            self._stickers[sticker_id] = sticker
            return None, saved.code
        return updated, ""

    def touch_used(self, sticker_id: str, *, now: float) -> None:
        """记一次使用。失败不回滚发送——台账是弱一致的一刻。"""
        sticker = self._stickers.get(sticker_id)
        if sticker is None:
            return
        self._stickers[sticker_id] = sticker.with_touch(now=now)
        self.save()

    def remove(self, sticker_id: str) -> str:
        """删除条目与文件。返回错误码（空 = 成功）。"""
        sticker = self._stickers.pop(sticker_id, None)
        if sticker is None:
            return ERR_NOT_FOUND
        saved = self.save()
        if not saved.ok:
            self._stickers[sticker_id] = sticker
            return saved.code
        try:
            self.image_path(sticker).unlink(missing_ok=True)
        except Exception:
            self._log(f"sticker file removal failed: id={sticker_id}")
        self._log(f"sticker removed: id={sticker_id}")
        return ""

    # ------------------------------------------------------------------
    # 自修复
    # ------------------------------------------------------------------

    def repair(self) -> dict[str, int]:
        """库体检：清掉文件已丢失的条目、删掉没人引用的孤儿文件、回填旧条目指纹。

        返回计数字典（面板如实展示）：removed_entries / purged_files / backfilled_hashes。
        纪律：只在自己生成的 `stickers/` 目录里活动；条目表就是引用集，
        不在表里的文件视为孤儿（目录里的文件全部由 add() 生成，无用户自放位）。
        """
        removed_entries = 0
        purged_files = 0
        backfilled = 0
        referenced: set[str] = set()
        dirty = False
        for sticker_id in list(self._stickers.keys()):
            sticker = self._stickers[sticker_id]
            path = self.image_path(sticker)
            if not path.is_file():
                self._stickers.pop(sticker_id)
                removed_entries += 1
                dirty = True
                continue
            referenced.add(sticker.file)
            if not sticker.sha256:
                try:
                    digest = content_sha256(path.read_bytes())
                except Exception:
                    continue  # 读不动的图：不回填也不删条目，交给下次体检
                self._stickers[sticker_id] = sticker.with_sha256(digest)
                backfilled += 1
                dirty = True
        if dirty:
            self.save()
        try:
            if self.stickers_dir.is_dir():
                for candidate in self.stickers_dir.iterdir():
                    if candidate.is_file() and candidate.name not in referenced:
                        try:
                            candidate.unlink()
                            purged_files += 1
                        except Exception:
                            self._log(f"orphan purge failed: {candidate.name}")
        except Exception:
            self._log("orphan scan failed")
        self._log(
            f"library repaired: entries_removed={removed_entries} "
            f"orphans_purged={purged_files} hashes_backfilled={backfilled}"
        )
        return {
            "removed_entries": removed_entries,
            "purged_files": purged_files,
            "backfilled_hashes": backfilled,
        }

    # ------------------------------------------------------------------
    # 使用台账
    # ------------------------------------------------------------------

    def usage_path(self) -> Path:
        return self._root / USAGE_FILENAME

    def read_usage(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.usage_path().read_text(encoding="utf-8"))
        except Exception:
            return []
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            return []
        out: list[dict[str, Any]] = []
        for item in entries[-limit:]:
            if isinstance(item, dict):
                out.append(dict(item))
        out.reverse()  # 新的在前
        return out

    def append_usage(self, record: dict[str, Any], *, keep: int) -> None:
        """追加一条使用记录（只放非隐私字段：时刻/表情 id/角色名/来源/成败）。

        落盘按时间正序（新的在尾部），`read_usage` 负责反转成"新的在前"；
        容量超过 keep 时从头部丢弃旧记录。
        """
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            entries: list[dict[str, Any]] = []
            try:
                raw = json.loads(self.usage_path().read_text(encoding="utf-8"))
                listed = raw.get("entries") if isinstance(raw, dict) else None
                if isinstance(listed, list):
                    entries = [dict(item) for item in listed if isinstance(item, dict)]
            except Exception:
                entries = []
            entries.append(dict(record))
            entries = entries[-max(20, keep):]
            payload = {"version": 1, "entries": entries}
            tmp = self.usage_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.usage_path())
        except Exception:
            self._log("usage ledger write failed")
