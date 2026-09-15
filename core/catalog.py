"""表情库的纯数据层：条目形状、格式嗅探、检索与给模型的目录文案。

设计决定：

1. **格式只认文件头，不认扩展名/文件名**——上传链路的 filename 来自客户端，
   不可信；`detect_image_format` 是唯一的判据来源。
1. **描述（desc）是主人给的短标签，梗义（caption）是她选图的使用依据**：目录行正文 caption 优先、
   空则回落 desc（`Sticker.catalog_body` 一把尺）；desc 仍必填、有长度上限。
2. **检索是"够用就好"**：子串匹配 desc/caption/tags/visible_text/id，不做分词、不做语义。
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
from dataclasses import dataclass, field, replace
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
# 梗义（v0.3.0，学习 外部系统的标注提示词 的数据层落点）：一到两句"这张图在回复什么、
# 什么上一句会触发发它"，不是画面描述。可选字段——老库无此键回空，目录行自动回落 desc。
CAPTION_MAX_CHARS = 300
# 图内原文（同理）：只进检索打分，**不进目录行**（避免把长图里的小字挤进她的注意力）。
VISIBLE_TEXT_MAX_CHARS = 200
TAG_MAX_CHARS = 24
TAGS_MAX_COUNT = 12
# 套图分组名（v0.2.0）：一条表情最多属于一个组；空串 = 未分组。
# 比标签宽一点——它是"套图/来源"这种成块的名字，不是散标签。
GROUP_MAX_CHARS = 40
# 单张表情图的字节上限：入口层（base64 解码后）与收件箱目录扫描共用同一个数字。
MAX_STICKER_BYTES = 8 * 1024 * 1024
# 预览分段大小（原始字节）：必须是 3 的倍数（base64 分段才能无填充拼接）。宿主入口回包走
# ZeroMQ 控制通道，单帧硬上限 4,784,128 字节（plugin/settings.py
# PLUGIN_ZMQ_CONTROL_UPLINK_MAX_BYTES，宿主源码实测：3.95MB 图整张 dataUrl=5.27MB
# 直接超出→响应被拒→宿主 15s 超时）。3MiB 原始→4MiB base64，给 JSON 封套留余量。
PREVIEW_CHUNK_BYTES = 3 * 1024 * 1024

# query 选图的候选上限（轮 C，与 外部系统 top_k=5 同量级）：头部并列时不替她拍板，
# 回一屏能读完的候选清单让她用 id 定夺。
SEND_CANDIDATES_MAX = 5

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


def normalize_group(value: Any) -> str:
    """把任意输入收敛成合法分组名：去空白、限长；非法/空一律回空串（=未分组）。

    纪律：分组**大小写敏感**（主人写的名字就是名字），但纯空白不算分组。
    """
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if not cleaned:
        return ""
    return cleaned[:GROUP_MAX_CHARS]


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


def validate_optional_text(value: Any, *, limit: int) -> tuple[str, str]:
    """可选长文本的共用尺（caption / visible_text 同一条）：
    非字符串/空白 → ("", "")（空是合法意图：未标注或清除）；超限 → too_long。"""
    if not isinstance(value, str):
        return "", ""
    cleaned = value.strip()
    if not cleaned:
        return "", ""
    if len(cleaned) > limit:
        return "", "too_long"
    return cleaned, ""


def normalize_optional_text(value: Any, *, limit: int) -> str:
    """宽松层的可选文本：去空白、硬截到 limit（manifest/持久化回填用——
    导入的目标是"先进来、坏了可后改"，不是复读入口校验，与 pack 的 desc 同纪律）。"""
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


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
    group: str = ""
    caption: str = ""
    visible_text: str = ""

    def catalog_body(self) -> str:
        """目录行正文：梗义优先，未标注时回落主人的短描述（一把尺，两处同源共用）。"""
        return self.caption or self.desc
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
            "group": self.group,
            "caption": self.caption,
            "visible_text": self.visible_text,
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
            group=normalize_group(raw.get("group")),
            caption=normalize_optional_text(raw.get("caption"), limit=CAPTION_MAX_CHARS),
            visible_text=normalize_optional_text(raw.get("visible_text"), limit=VISIBLE_TEXT_MAX_CHARS),
        )

    def with_touch(self, *, now: float) -> "Sticker":
        """记一次使用的副本。v0.3.0 起用 replace：手工枚举字段的拷贝方法每加一个
        字段就多一个"漏了就丢数据"的雷（sha256 回归的真病根），replace 从构造上灭掉。"""
        return replace(self, use_count=self.use_count + 1, last_used_at=now)

    def with_sha256(self, digest: str) -> "Sticker":
        """补指纹的副本（v0.1.1 前的旧条目回填用；frozen dataclass 不改原地对象）。"""
        return replace(self, sha256=digest)


def search_with_scores(
    stickers: list[Sticker], query: str, *, include_disabled: bool = False
) -> list[tuple[int, Sticker]]:
    """带分数的检索（唯一实现）：打分+排序都在这；`search_stickers` 与
    `resolve_send_target` 是它的薄封装——一把尺，三处同源。

    排序：命中分（精确 id > desc > caption > 套图精确 > 标签 > 套图子串 >
    图内原文 > 文件名）> 使用数 > 最近时刻。空查询 = 全量按"她最近爱用"，
    分数全记 0（没命中可言）。
    """
    pool = [s for s in stickers if include_disabled or not s.disabled]
    term = (query or "").strip().casefold()
    if not term:
        ranked = sorted(pool, key=lambda s: (-s.use_count, -s.last_used_at))
        return [(0, s) for s in ranked]
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
    return scored


def search_stickers(
    stickers: list[Sticker], query: str, *, include_disabled: bool = False
) -> list[Sticker]:
    """按查询串过滤并排序；规则见 `search_with_scores`。"""
    return [
        sticker for _, sticker in search_with_scores(stickers, query, include_disabled=include_disabled)
    ]


def resolve_send_target(
    stickers: list[Sticker], query: str, *, limit: int = SEND_CANDIDATES_MAX
) -> tuple[Sticker | None, list[Sticker]]:
    """query 选图的判定器：返回 (唯一最优, 并列候选)，两者至多一个非空。

    轮 C 的体验核心：**不替她拍板并列**。最优分严格唯一→直发；
    头部并列→回 top-K 候选让她拿 id 再发。同分同台账的两张确实分不出高下——
    猜一张也是发得，但把选择权还给她更符合"表情是她挑的"这件事。
    空查询/全没命中都回 (None, [])。
    """
    term = (query or "").strip()
    if not term:
        return None, []
    scored = search_with_scores(stickers, term)
    if not scored:
        return None, []
    top_score = scored[0][0]
    if len(scored) == 1 or scored[1][0] < top_score:
        return scored[0][1], []
    return None, [sticker for _, sticker in scored[: max(1, limit)]]


def _match_score(sticker: Sticker, term: str) -> int:
    if sticker.id == term:
        return 100
    folded_desc = sticker.desc.casefold()
    if term in folded_desc:
        return 80
    folded_caption = sticker.caption.casefold()
    if term == folded_caption:
        return 78
    if term in folded_caption:
        return 76
    if sticker.group and term == sticker.group.casefold():
        return 75
    for tag in sticker.tags:
        if term == tag.casefold():
            return 70
        if term in tag.casefold():
            return 60
    if sticker.group and term in sticker.group.casefold():
        return 58
    if sticker.visible_text and term in sticker.visible_text.casefold():
        return 40
    if term in sticker.file.casefold():
        return 30
    return 0


def format_catalog_for_model(stickers: list[Sticker], limit: int) -> str:
    """给模型看的目录（`sticker_list` 工具、存在感注入与面板刷新共用同一份文案）。

    一行一条：`[id] 梗义或描述（套图：G；标签：a/b）`——正文走 `catalog_body` 一把尺
    （caption 优先，v0.3.0）。不含文件名、不含图内原文、不含计数——
    那些是给人看的账本信息，进了提示词只会挤占她的注意力。
    """
    lines: list[str] = []
    pool = [s for s in stickers if not s.disabled]
    for sticker in pool[: max(0, limit)]:
        line = f"[{sticker.id}] {sticker.catalog_body()}"
        brackets: list[str] = []
        if sticker.group:
            brackets.append(f"套图：{sticker.group}")
        if sticker.tags:
            brackets.append(f"标签：{'/'.join(sticker.tags)}")
        if brackets:
            line += f"（{'；'.join(brackets)}）"
        lines.append(line)
    return "\n".join(lines)
