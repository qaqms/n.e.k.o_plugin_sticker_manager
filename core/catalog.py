"""表情库的纯数据层：条目形状、格式嗅探、检索与给模型的目录文案。

设计决定：

1. **格式只认文件头，不认扩展名/文件名**——上传链路的 filename 来自客户端，
   不可信；`detect_image_format` 是唯一的判据来源。
2. **描述（desc）是模型选图的唯一依据**，所以它必填、有长度上限，
   且在目录里排在标签前面。
3. **检索是"够用就好"**：子串匹配 desc/tags/id，不做分词、不做语义。
   真语义检索是宿主记忆层的事，不在插件里造第二套。
4. 目录文案的纪律沿用 our_life 的注入契约：不给模型看原始路径、
   不堆砌形容词，一行一条。
5. **查重只认内容指纹（sha256），不认文件名/扩展名**——与魔数嗅探同理，
   文件名来自客户端，不可信。
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable

# 支持的表情包格式（文件头 → 规范扩展名）。bmp 被排除：宿主 images.upload
# 的归一链路对 bmp 没有优势，且没有表情包生态真的用 bmp。
_MAGIC_FORMATS: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)

DESC_MAX_CHARS = 200
TAG_MAX_CHARS = 24
TAGS_MAX_COUNT = 12
# 单张表情图的字节上限：入口层（base64 解码后）与收件箱目录扫描共用同一个数字。
MAX_STICKER_BYTES = 8 * 1024 * 1024

# 收件箱导入时文件名清洗成描述的规则：删扩展名、分隔符换空格、连续空白压扁。
# 面板浏览器侧有一份等价的几行小函数；那是跨运行时的重复（iframe 里碰不到
# Python），不抽同进程方法——改规则时两边一起改（DESIGN.md 陷阱 11）。
_DESC_SEPARATORS = re.compile(r"[_\-+.]+")
_DESC_BLANKS = re.compile(r"\s+")


def desc_from_filename(name: str) -> str:
    """把文件名（可带路径）洗成一句能当描述的骨干；洗空了给稳定兜底串。"""
    base = (name or "").replace("\\", "/").rstrip("/").split("/")[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    cleaned = _DESC_BLANKS.sub(" ", _DESC_SEPARATORS.sub(" ", stem)).strip()
    if not cleaned:
        return "sticker"  # 空骨干兜底：desc 必填是硬契约，这里造一个中性占位
    return cleaned[:DESC_MAX_CHARS]


def detect_image_format(data: bytes) -> tuple[str, str] | None:
    """嗅探图片格式。返回 (扩展名, mime)，不是受支持格式则返回 None。

    webp 需要检查 RIFF....WEBP 的双段结构（前 4 字节 RIFF、8-12 字节 WEBP），
    没法用单一前缀表达，所以单独分支。
    """
    for prefix, ext, mime in _MAGIC_FORMATS:
        if data.startswith(prefix):
            return ext, mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def is_animated_gif(data: bytes) -> bool:
    """粗判 gif 是否多帧（含多个 GIF87a/89a 头或 Application Extension 里的 NETSCAPE）。

    刻意保守：只用于决定"要不要为动图保留内联通道"的日志与提示，
    不参与发送方式判定（所有 gif 都走内联，见 §设计决定 与 settings.send）。
    """
    return data[:6] in (b"GIF87a", b"GIF89a") and (
        data.count(b"\x00\x21\xf9\x04") > 1 or b"NETSCAPE2.0" in data[:4096]
    )


def content_sha256(data: bytes) -> str:
    """图片本体的内容指纹（查重用）。放 core 层：入口、持久层、repair 共用同一算法。"""
    return hashlib.sha256(data or b"").hexdigest()


def new_sticker_id(existing: Iterable[str]) -> str:
    """生成长度 10 的十六进制 id；与现有条目撞车则重采样。"""
    taken = set(existing)
    for _ in range(16):
        candidate = secrets.token_hex(5)
        if candidate not in taken:
            return candidate
    raise RuntimeError("sticker id space collision")


def normalize_tags(tags: Any) -> list[str]:
    """把任意输入收敛成合法标签列表：去空白、去重、限长限量。"""
    if not isinstance(tags, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        if not isinstance(raw, str):
            continue
        tag = raw.strip()
        if not tag or len(tag) > TAG_MAX_CHARS:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= TAGS_MAX_COUNT:
            break
    return out


def parse_tags_field(value: Any) -> list[str]:
    """入口参数里的 tags 允许两种形状：字符串列表，或逗号/顿号分隔的单串。"""
    if isinstance(value, list):
        return normalize_tags(value)
    if isinstance(value, str):
        parts = value.replace("，", ",").replace("、", ",").split(",")
        return normalize_tags(parts)
    return []


def validate_desc(desc: Any) -> tuple[str, str]:
    """返回 (规范化描述, 错误码)。错误码是稳定 ASCII：empty / too_long。"""
    if not isinstance(desc, str) or not desc.strip():
        return "", "empty"
    cleaned = desc.strip()
    if len(cleaned) > DESC_MAX_CHARS:
        return "", "too_long"
    return cleaned, ""


@dataclass(frozen=True)
class Sticker:
    """一条表情包记录（目录内存形状；文件在 stickers/<id>.<ext>）。"""

    id: str
    file: str
    desc: str
    tags: list[str] = field(default_factory=list)
    disabled: bool = False
    added_at: float = 0.0
    use_count: int = 0
    last_used_at: float = 0.0
    sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "desc": self.desc,
            "tags": list(self.tags),
            "disabled": self.disabled,
            "added_at": self.added_at,
            "use_count": self.use_count,
            "last_used_at": self.last_used_at,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Sticker | None":
        """从持久化 dict 宽松还原；形状不对就丢弃（返回 None），不炸整本目录。"""
        if not isinstance(raw, dict):
            return None
        sid = raw.get("id")
        name = raw.get("file")
        desc = raw.get("desc")
        if not isinstance(sid, str) or not sid or not isinstance(name, str) or not name:
            return None
        if not isinstance(desc, str):
            desc = ""
        return cls(
            id=sid,
            file=name,
            desc=desc,
            tags=normalize_tags(raw.get("tags")),
            disabled=bool(raw.get("disabled", False)),
            added_at=float(raw.get("added_at") or 0.0),
            use_count=int(raw.get("use_count") or 0),
            last_used_at=float(raw.get("last_used_at") or 0.0),
            sha256=raw.get("sha256") if isinstance(raw.get("sha256"), str) else "",
        )

    def with_touch(self, *, now: float) -> "Sticker":
        return Sticker(
            id=self.id,
            file=self.file,
            desc=self.desc,
            tags=list(self.tags),
            disabled=self.disabled,
            added_at=self.added_at,
            use_count=self.use_count + 1,
            last_used_at=now,
            sha256=self.sha256,
        )

    def with_sha256(self, digest: str) -> "Sticker":
        """补指纹的副本（v0.1.1 前的旧条目回填用；frozen dataclass 不改原地对象）。"""
        return Sticker(
            id=self.id,
            file=self.file,
            desc=self.desc,
            tags=list(self.tags),
            disabled=self.disabled,
            added_at=self.added_at,
            use_count=self.use_count,
            last_used_at=self.last_used_at,
            sha256=digest,
        )


def search_stickers(
    stickers: list[Sticker], query: str, *, include_disabled: bool = False
) -> list[Sticker]:
    """按查询串过滤并按 (精确 id > desc 命中 > tag 命中, 最近使用, 使用次数) 排序。

    空查询 = 全量（含禁用的除外，除非显式要求），按"她最近爱用"排序——
    模型拿到空查询时想要的就是"常货"。
    """
    pool = [s for s in stickers if include_disabled or not s.disabled]
    term = (query or "").strip().casefold()
    if not term:
        return sorted(pool, key=lambda s: (-s.use_count, -s.last_used_at))
    scored: list[tuple[int, Sticker]] = []
    for sticker in pool:
        score = _match_score(sticker, term)
        if score > 0:
            scored.append((score, sticker))
    scored.sort(
        key=lambda pair: (
            -pair[0],
            -pair[1].use_count,
            -pair[1].last_used_at,
        )
    )
    return [sticker for _, sticker in scored]


def _match_score(sticker: Sticker, term: str) -> int:
    if sticker.id == term:
        return 100
    folded_desc = sticker.desc.casefold()
    if term in folded_desc:
        return 80
    for tag in sticker.tags:
        if term == tag.casefold():
            return 70
        if term in tag.casefold():
            return 60
    if term in sticker.file.casefold():
        return 30
    return 0


def format_catalog_for_model(stickers: list[Sticker], limit: int) -> str:
    """给模型看的目录（`sticker_list` 工具与目录注入共用同一份文案）。

    一行一条：`[id] 描述（标签：a/b）`。不含文件名、不含计数——
    那些是给人看的账本信息，进了提示词只会挤占她的注意力。
    """
    lines: list[str] = []
    pool = [s for s in stickers if not s.disabled]
    for sticker in pool[: max(0, limit)]:
        line = f"[{sticker.id}] {sticker.desc}"
        if sticker.tags:
            line += f"（标签：{'/'.join(sticker.tags)}）"
        lines.append(line)
    return "\n".join(lines)
