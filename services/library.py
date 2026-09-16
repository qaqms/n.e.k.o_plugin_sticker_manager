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
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..core.catalog import (
    GROUP_DESC_MAX_CHARS,
    MAX_STICKER_BYTES,
    UPLOAD_CHUNK_BYTES,
    UPLOAD_MAX_TOTAL_BYTES,
    Sticker,
    content_sha256,
    desc_from_filename,
    detect_image_format,
    new_sticker_id,
    normalize_group,
)
from ..core.pack import (
    PACK_DIR_PREFIX,
    PACK_MANIFEST_FILENAME,
    PACK_MAX_ENTRIES,
    build_manifest,
    parse_manifest,
    parse_manifest_groups,
    safe_member_name,
)

CATALOG_VERSION = 1
CATALOG_FILENAME = "catalog.json"
USAGE_FILENAME = "usage.json"
STICKER_DIRNAME = "stickers"
# 收件箱：用户把一堆图片文件（或套图 zip 包）丢这里，面板点"导入收件箱"逐张收进库。
# 选目录而不是浏览器多选当唯一批量通道：免 base64 膨胀、免逐次往返、
# 成百张也不怕；点不到的文件（隐藏/子目录）一律不碰不删。
INBOX_DIRNAME = "inbox"
# 直传暂存（v0.6.0 面板选择文件导入）：分块落盘在这里，完成即导入、随即删。
# 与 inbox 的分工：inbox 是"主人自己找到了目录"的旁路，uploads 是面板会话的
# 临时尸体——两者的文件都不该长期住下，但只有 inbox 参与"点导入"扫描。
UPLOADS_DIRNAME = "uploads"
# 导出物（v0.2.0）：套图 zip 落这里。面板拿不到文件句柄（iframe），
# 只能显示路径让主人自己取——与 inbox 是一对镜像（进/出都走文件系统）。
EXPORTS_DIRNAME = "exports"

# 稳定错误码（面板与模型各自翻译/理解，见 DESIGN.md 的错误码契约）
ERR_IO = "library_io_error"
ERR_NOT_FOUND = "sticker_not_found"
ERR_EMPTY = "library_empty"
ERR_DUPLICATE = "duplicate_image"
ERR_PACK_UNREADABLE = "pack_unreadable"


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
        # 分组说明（轮 F：“分类=描述”挂在组上，不挂在图上）；catalog.json 顶层 groups。
        self._groups: dict[str, str] = {}
        self._loaded = False
        self._io_dirty = False
        # zip 直传会话（仅本进程，sid -> {h, path, seq, size, name, at}）：
        # 入口调用都跑在同一事件循环上，字典变更天然串行；进程重启 = 会话作废，
        # 重选文件即可——断点续传不值得为这种短生命周期交互复杂度。
        self._uploads: dict[str, dict] = {}

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
    def inbox_dir(self) -> Path:
        return self._root / INBOX_DIRNAME

    def inbox_files(self) -> list[Path]:
        """收件箱里可见的候选文件（排序稳定；不递归、不碰隐藏项）。"""
        try:
            if not self.inbox_dir.is_dir():
                return []
            return sorted(p for p in self.inbox_dir.iterdir() if p.is_file() and not p.name.startswith("."))
        except Exception:
            self._log("inbox scan failed")
            return []

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
        self._groups = {}
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
        # 分组说明宽松读（轮 F）：坏键/坏值跳过，空说明不存；旧库无此键 = 空字典。
        groups_raw = raw.get("groups") if isinstance(raw, dict) else None
        if isinstance(groups_raw, dict):
            for key, value in groups_raw.items():
                group = normalize_group(key)
                if not group or not isinstance(value, str):
                    continue
                desc = value.strip()[:GROUP_DESC_MAX_CHARS]
                if desc:
                    self._groups[group] = desc
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
                "groups": dict(self._groups),
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
    # 分组说明（v0.7.0 轮 F）
    # ------------------------------------------------------------------

    def group_descs(self) -> dict[str, str]:
        """组名 -> 一句话说明（副本）。"""
        return dict(self._groups)

    def set_group_desc(self, name: Any, desc: Any) -> tuple[bool, str]:
        """写/改/清一个分组的说明；回 (是否成功, 错误码)。

        合法性在入口层把关（group_required / group_desc_too_long），这里只兕宽钳位；
        空说明 = 清除意图。写盘失败回滚内存，不把假成功留给面板。
        """
        group = normalize_group(name)
        if not group:
            return False, "group_required"
        cleaned = desc.strip()[:GROUP_DESC_MAX_CHARS] if isinstance(desc, str) else ""
        previous = self._groups.get(group, "")
        if cleaned:
            self._groups[group] = cleaned
        else:
            self._groups.pop(group, None)
        saved = self.save()
        if not saved.ok:
            if previous:
                self._groups[group] = previous
            else:
                self._groups.pop(group, None)
            return False, saved.code or ERR_IO
        return True, ""

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def all(self) -> list[Sticker]:
        return sorted(
            self._stickers.values(),
            key=lambda s: -s.added_at,
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
        group: str = "",
        caption: str = "",
        visible_text: str = "",
    ) -> tuple[Sticker | None, str]:
        """入库一张图。返回 (条目, 错误码)；成功时错误码为空。

        错误码：invalid_image（不是受支持的图片格式）/ duplicate_image（库里已有同图）/ io_error。
        查重只认内容指纹，不认文件名（见 core/catalog 设计决定 5）。
        group/caption/visible_text 由入口层收敛后才进来（core 层负责合法性，这里只搬运）。
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
            group=group,
            caption=caption,
            visible_text=visible_text,
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
        group: str | None = None,
        caption: str | None = None,
        visible_text: str | None = None,
    ) -> tuple[Sticker | None, str]:
        """改描述/标签/禁用态/分组/梗义/图内文字；None = 不改那一项（合法性由入口层把关）。

        v0.3.0 起用 dataclasses.replace 重建：sha256 回归（v0.1.x 每编辑一次丢一次指纹，
        当时靠手工补字段治的）的真病根是"手工枚举字段的拷贝重建"——每加一个字段
        就多一个漏写即丢数据的雷，replace 从构造上灭掉整类雷。
        """
        sticker = self._stickers.get(sticker_id)
        if sticker is None:
            return None, ERR_NOT_FOUND
        patch: dict[str, Any] = {}
        if desc is not None:
            patch["desc"] = desc
        if tags is not None:
            patch["tags"] = list(tags)
        if disabled is not None:
            patch["disabled"] = bool(disabled)
        if group is not None:
            patch["group"] = group
        if caption is not None:
            patch["caption"] = caption
        if visible_text is not None:
            patch["visible_text"] = visible_text
        updated = replace(sticker, **patch) if patch else sticker
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
    # 套图包导出 / 导入（v0.2.0）
    # ------------------------------------------------------------------

    @property
    def exports_dir(self) -> Path:
        return self._root / EXPORTS_DIRNAME

    def export_pack(self, *, now: float | None = None) -> tuple[dict[str, Any], str]:
        """把整本库打成套图 zip 写进 `exports/`。返回 (结果, 错误码)。

        结果是给面板的小回包：`{"file": 路径, "exported": n, "skipped": n}`——
        **绝不回包字节**（ZeroMQ 控制帧 4.56MiB 上限，见 DESIGN 陷阱 14；
        图字节只进磁盘，不进返回值）。
        """
        moment = time.time() if now is None else now
        stickers = self.all()
        if not stickers:
            return {}, ERR_EMPTY
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(moment))
        try:
            self.exports_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return {}, ERR_IO
        target = self.exports_dir / f"stickers-{stamp}.zip"
        suffix = 0
        while target.exists():  # 同一秒连点两次导出：如实换名，不覆盖
            suffix += 1
            target = self.exports_dir / f"stickers-{stamp}-{suffix}.zip"
        exported = 0
        skipped = 0
        included: list[Sticker] = []
        for sticker in stickers:
            try:
                self.image_path(sticker).read_bytes()  # 只验可读，字节由 zip 自己写
            except Exception:
                skipped += 1
                continue
            included.append(sticker)
            exported += 1
        try:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as pack:
                pack.writestr(
                    PACK_MANIFEST_FILENAME,
                    json.dumps(build_manifest(included, self.group_descs()), ensure_ascii=False, indent=2),
                )
                for sticker in included:
                    pack.write(self.image_path(sticker), arcname=f"{PACK_DIR_PREFIX}{sticker.file}")
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            return {}, ERR_IO
        self._log(f"pack exported: file={target.name} stickers={exported} skipped={skipped}")
        return {"file": str(target), "exported": exported, "skipped": skipped}, ""

    def import_pack(self, path: Path, *, group: str = "", tags: list[str] | None = None) -> dict[str, int]:
        """导入一个套图 zip（manifest 协议见 core/pack）。返回与收件箱同款四类计数。

        纪律：
        - **zip-slip**：条目名过 `safe_member_name`（core 层唯一的门），目录/上跳/隐藏一律拒；
        - 单张字节上限先查 `ZipInfo.file_size` 再读（防 zip bomb 把解压撑进内存）；
        - 图字节仍走 add 的全套规则（魔数/查重/原子写盘），不造第二条入库路；
        - 有 manifest 认 manifest（描述/标签/分组随行就市），没有就按裸图集处理，
          描述取文件名清洗——与收件箱单文件通道同一形态；两种形态都吃整批 tags/group 兜底。
        """
        summary = {"imported": 0, "duplicates": 0, "rejected": 0, "failed": 0}
        try:
            pack = zipfile.ZipFile(path)
        except Exception:
            self._log(f"pack unreadable: {path.name}")
            summary["failed"] += 1
            return summary
        with pack:
            try:
                manifest_raw = json.loads(pack.read(PACK_MANIFEST_FILENAME).decode("utf-8"))
            except KeyError:
                manifest_raw = None
            except Exception:
                # manifest 在但坏了：图仍可救（按裸包处理），如实记一笔日志。
                self._log(f"pack manifest broken, falling back to bare mode: {path.name}")
                manifest_raw = None
            parsed = parse_manifest(manifest_raw) if manifest_raw is not None else []
            pack_groups = parse_manifest_groups(manifest_raw) if manifest_raw is not None else {}
            imported_groups: set[str] = set()
            if parsed:
                members = {f"{PACK_DIR_PREFIX}{entry.file}": entry for entry in parsed}
            else:
                members = {name: None for name in pack.namelist() if safe_member_name(name)}
            if len(members) > PACK_MAX_ENTRIES:
                # 超大包：收下前 PACK_MAX_ENTRIES 个，多出的整批计 rejected。
                summary["rejected"] += len(members) - PACK_MAX_ENTRIES
                members = dict(list(members.items())[:PACK_MAX_ENTRIES])
            for name, entry in members.items():
                try:
                    info = pack.getinfo(name)
                except KeyError:
                    summary["failed"] += 1  # manifest 报了名但包里没有：如实计
                    continue
                if info.is_dir() or info.file_size > MAX_STICKER_BYTES:
                    summary["rejected"] += 1
                    continue
                try:
                    data = pack.read(name)
                except Exception:
                    summary["failed"] += 1
                    continue
                file_name = safe_member_name(name)
                entry_group = entry.group if entry is not None else ""
                entry_tags = list(entry.tags) if entry is not None and entry.tags else list(tags or [])
                sticker, error = self.add(
                    data=data,
                    desc=entry.desc if entry is not None else desc_from_filename(file_name),
                    tags=entry_tags,
                    group=entry_group or group,
                    caption=entry.caption if entry is not None else "",
                    visible_text=entry.visible_text if entry is not None else "",
                    now=time.time(),
                )
                if error == ERR_DUPLICATE:
                    summary["duplicates"] += 1
                elif error:
                    summary["rejected"] += 1
                else:
                    summary["imported"] += 1
                    if sticker is not None and sticker.group:
                        imported_groups.add(sticker.group)
            # 分组说明随包迁移（轮 F）：只补缺不覆盖——主人已写过的组话不被包/import 消音。
            changed = False
            for group_name in imported_groups:
                group_desc = pack_groups.get(group_name, "")
                if group_desc and not self._groups.get(group_name):
                    self._groups[group_name] = group_desc
                    changed = True
            if changed:
                self.save()
        self._log(
            "pack ingested: {} imported={imported} duplicates={duplicates} rejected={rejected} failed={failed}".format(
                path.name, **summary
            )
        )
        return summary

    # ------------------------------------------------------------------
    # 收件箱导入
    # ------------------------------------------------------------------

    def ingest_inbox(
        self,
        *,
        tags: list[str],
        group: str = "",
        max_bytes: int = MAX_STICKER_BYTES,
    ) -> dict[str, int]:
        """把收件箱里的图片逐张收进库（描述取自文件名，走 add 的全部规则：
        魔数、查重、原子写盘）。`.zip` 按套图包整批收（v0.2.0）。

        处置纪律：成功与重复的源文件删掉（重复件留着只会在下次体检里再报一遍）；
        超限/坏图/读不动的**保留原地**，让用户能改好后重试。
        zip 包的纪律是同一条尺的整包版：包内**有任何**未收下的（rejected/failed）
        就留包原地——已收下的图有指纹，重试整包时它们只会计"重复"，不重复入库。
        返回四类计数：imported / duplicates / rejected / failed（隐藏文件不计）。
        """
        summary = {"imported": 0, "duplicates": 0, "rejected": 0, "failed": 0}
        for path in self.inbox_files():
            if path.suffix.lower() == ".zip":
                pack_summary = self.import_pack(path, group=group, tags=tags)
                if not (pack_summary["rejected"] or pack_summary["failed"]):
                    self._discard_inbox_file(path)
                for key in summary:
                    summary[key] += pack_summary[key]
                continue
            try:
                data = path.read_bytes()
            except Exception:
                summary["failed"] += 1
                continue
            if len(data) > max_bytes:
                summary["rejected"] += 1
                continue
            sticker, error = self.add(
                data=data,
                desc=desc_from_filename(path.name),
                tags=tags,
                group=group,
                now=time.time(),
            )
            if error == ERR_DUPLICATE:
                summary["duplicates"] += 1
                self._discard_inbox_file(path)
            elif error:
                summary["rejected"] += 1  # invalid_image / io_error：文件留着
            else:
                summary["imported"] += 1
                self._discard_inbox_file(path)
        self._log(
            "inbox ingested: imported={imported} duplicates={duplicates} rejected={rejected} failed={failed}".format(
                **summary
            )
        )
        return summary

    def _discard_inbox_file(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            self._log(f"inbox file discard failed: {path.name}")

    # ------------------------------------------------------------------
    # zip 直传会话（v0.6.0：面板“选择文件”导入，不再依赖“放收件箱 + 点读取”）
    # ------------------------------------------------------------------
    # 为什么分块：面板→entry 的 args 与预览回包走同一条 ZeroMQ 控制通道，
    # 单帧硬上限 4,784,128 字节（见 core.catalog PREVIEW_CHUNK_BYTES 注释）——
    # 几 MiB 的套图包整块 base64 会被直接拒。方向相反：预览是服务端分段**回**，
    # 这里是面板分段**来**。会话只存本进程内存 + data/uploads/.sid.part：
    # 重启即作废，重选文件即可（断点续传不值得为这种短生命周期交互引入复杂度）。

    @property
    def uploads_dir(self) -> Path:
        return self._root / UPLOADS_DIRNAME

    def upload_start(self, filename: Any, size: Any = None) -> tuple[str, str]:
        """开一个上传会话：只认 .zip 结尾（图走 add 通道，不重复造第二条入库路）。

        回 (session_id, 错误码)；错误码为空串 = 成功。可选的 size 只用来
        提前拒绝“一眼就知道装不下”的包（真正的总量上限在每块 append 时把关）。
        """
        name = safe_member_name(filename)
        # safe_member_name 对"../x.zip"是**剥成 x.zip**而非拒——目录形状在这里没有合法用途
        # （面板传来的就该是裸文件名），收到带路径的名宁可拒也不静默改写：诚实 > 宽容。
        if not isinstance(filename, str) or "/" in filename or "\\" in filename:
            return "", "upload_not_zip"
        if not name or not name.lower().endswith(".zip"):
            return "", "upload_not_zip"
        if isinstance(size, int) and not isinstance(size, bool) and size > UPLOAD_MAX_TOTAL_BYTES:
            return "", "upload_too_large"
        self._gc_uploads()
        sid = new_sticker_id(set(self._uploads))
        path = self.uploads_dir / f".{sid}.part"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("wb")
        except Exception:
            self._log("upload staging open failed")
            return "", "upload_write_failed"
        self._uploads[sid] = {"h": handle, "path": path, "seq": 0, "size": 0, "name": name, "at": time.time()}
        return sid, ""

    def upload_append(self, sid: str, seq: Any, data: bytes) -> str:
        """按 seq 顺序追加一块；乱序/超限/写失败都**作废会话**（不留半截尸体让人重试错对象）。

        回错误码；空串 = 成功。seq 必须连续——面板循环保证；乱序只可能
        是网络重放/并发错乱，宁可作废重选也不能静默拼出一个坏 zip。
        """
        session = self._uploads.get(sid) if isinstance(sid, str) else None
        if session is None:
            return "upload_session_unknown"
        if seq != session["seq"]:
            self._drop_upload(sid)
            return "upload_seq_gap"
        if len(data) > UPLOAD_CHUNK_BYTES or session["size"] + len(data) > UPLOAD_MAX_TOTAL_BYTES:
            self._drop_upload(sid)
            return "upload_too_large"
        try:
            session["h"].write(data)
        except Exception:
            self._drop_upload(sid)
            self._log("upload chunk write failed")
            return "upload_write_failed"
        session["seq"] = session["seq"] + 1
        session["size"] += len(data)
        session["at"] = time.time()
        return ""

    def upload_finish(self, sid: str, *, tags: list[str] | None = None, group: str = "") -> tuple[dict[str, int], str]:
        """收尾：关流→同一把尺 import_pack→删暂存体。

        与 inbox “成功即删/失败留原地重试”不同：直传会话没有“原地”——面板会话
        是一次性的，无论导入结果如何都删暂存体（成败已进 summary 四类计数，
        重试 = 重选文件）。包内部分失败不拼掉整次上传：计数如实回。
        """
        session = self._uploads.pop(sid, None) if isinstance(sid, str) else None
        if session is None:
            return {}, "upload_session_unknown"
        try:
            session["h"].close()
        except Exception:
            pass
        if session["size"] == 0:
            self._drop_upload_file(session["path"])
            return {}, "upload_empty"
        summary = self.import_pack(session["path"], tags=tags, group=group)
        self._drop_upload_file(session["path"])
        return summary, ""

    def _drop_upload(self, sid: str) -> None:
        session = self._uploads.pop(sid, None)
        if session is not None:
            try:
                session["h"].close()
            except Exception:
                pass
            self._drop_upload_file(session["path"])

    @staticmethod
    def _drop_upload_file(path: Path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass

    def _gc_uploads(self, *, older_than_sec: float = 24 * 3600) -> None:
        """新开会话前顺带扫死体：内存会话按 at 过期；盘上残留的 .*.part（上次崩溃/断电）
        按 mtime 过期。隐藏名开头 + 固定后缀，不伤及任何正经文件。"""
        now = time.time()
        for sid in list(self._uploads):
            if now - self._uploads[sid]["at"] > older_than_sec:
                self._drop_upload(sid)
        try:
            if self.uploads_dir.is_dir():
                for path in self.uploads_dir.iterdir():
                    if not path.name.startswith(".") or not path.name.endswith(".part"):
                        continue
                    try:
                        if path.is_file() and now - path.stat().st_mtime > older_than_sec:
                            path.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            self._log("uploads gc scan failed")

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
            entries = entries[-max(20, keep) :]
            payload = {"version": 1, "entries": entries}
            tmp = self.usage_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.usage_path())
        except Exception:
            self._log("usage ledger write failed")
