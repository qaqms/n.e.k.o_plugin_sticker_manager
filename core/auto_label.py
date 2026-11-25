"""自动识图的判定尺（纯函数、零 IO）：模型回答怎么解析、哪些字段能写、写成什么样。

三条尺：
1. **主人优先**：主人改过的字段（`owner_edited`，措辞 desc/caption 绑成一块）一律不动；
2. **只填空**：空字段才写；之前由模型写的字段（`auto_fields`）允许重新识别时覆盖；
3. **分类只从已有的里选**：图在“未分组”时直接放进模型建议的分类；已有分类的只记一条建议，不挪。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

from .catalog import (
    CAPTION_MAX_CHARS,
    DESC_MAX_CHARS,
    VISIBLE_TEXT_MAX_CHARS,
    Sticker,
    normalize_group,
    normalize_tags,
)
from .labeling import protected_fields

NAME_MAX_CHARS = 16
TAGS_MAX = 8

# 导入时从文件名洗出来的短名大多是 “IMG 2023 0412”“sticker”“a1b2c3” 这种没意义的串；
# 带中文或别的文字的文件名通常是主人起的名字，留着。
_MACHINE_NAME = re.compile(r"^[A-Za-z0-9 ]*$")


def build_prompt(groups: Mapping[str, str], current_group: str, *, animated: bool) -> str:
    """给识图模型的话。分类表只放图所在区的分类，当前分类只当参考。"""
    lines = [
        "你在给聊天表情包做标注，标注结果会帮助另一个模型在聊天时挑图。只根据图片本身判断。",
    ]
    if animated:
        lines.append("这是一张动图，按时间顺序取了几帧拼在一起（从左到右、从上到下），把它当成一个整体来理解。")
    lines += [
        "只返回一个 JSON 对象，不要 Markdown，不要解释：",
        '{"name":"不超过8个字的短名",'
        '"caption":"一两句：发这张图的人在表达什么态度或情绪，适合回应什么样的话",'
        '"visible_text":"图里清晰可见的文字原文，没有就空字符串",'
        '"tags":["4到8个中文短标签，写情绪、动作和对象"],'
        '"group":"从下面的分类里选一个最合适的名字，都不合适就空字符串"}',
    ]
    if groups:
        lines.append("可选分类：")
        for name, desc in groups.items():
            lines.append(f"- {name}：{desc}" if desc else f"- {name}")
    else:
        lines.append("可选分类：无（group 填空字符串）")
    if current_group:
        lines.append(f"这张图现在在「{current_group}」，只作参考，不合适可以选别的。")
    return "\n".join(lines)


def parse_answer(content: Any) -> dict[str, Any]:
    """从模型回答里取出 JSON；拿不到就回空 dict（调用方记一次失败）。"""
    if not isinstance(content, str):
        return {}
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    caption = _text(data.get("caption"), CAPTION_MAX_CHARS)
    if not caption:
        return {}  # 梗义是这次识图的主产物：没有它就算没答好
    tags = data.get("tags")
    return {
        "name": _text(data.get("name"), NAME_MAX_CHARS),
        "caption": caption,
        "visible_text": _text(data.get("visible_text"), VISIBLE_TEXT_MAX_CHARS),
        "tags": normalize_tags([str(tag) for tag in tags])[:TAGS_MAX] if isinstance(tags, list) else [],
        "group": normalize_group(data.get("group")),
    }


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def is_machine_name(desc: str) -> bool:
    return not desc or bool(_MACHINE_NAME.match(desc))


def plan_patch(sticker: Sticker, answer: Mapping[str, Any], allowed_groups: Iterable[str]) -> dict[str, Any]:
    """算出要写回这张图的字段（含 label_state=done 与 auto_fields）。"""
    keep = protected_fields(sticker)
    auto = set(sticker.auto_fields)
    patch: dict[str, Any] = {}
    written: set[str] = set()

    def writable(name: str, current: Any) -> bool:
        return name not in keep and (not current or name in auto)

    for name in ("caption", "visible_text"):
        value = answer.get(name) or ""
        if value and writable(name, getattr(sticker, name)):
            patch[name] = value
            written.add(name)
    tags = list(answer.get("tags") or [])
    if tags and writable("tags", sticker.tags):
        patch["tags"] = tags
        written.add("tags")
    title = answer.get("name") or ""
    if title and "desc" not in keep and ("desc" in auto or is_machine_name(sticker.desc)):
        patch["desc"] = title[:DESC_MAX_CHARS]
        written.add("desc")
    suggested = answer.get("group") or ""
    allowed = set(allowed_groups)
    patch["suggested_group"] = ""
    if suggested in allowed and suggested != sticker.group:
        if not sticker.group and "group" not in keep:
            patch["group"] = suggested
            written.add("group")
        else:
            patch["suggested_group"] = suggested
    patch["auto_fields"] = sorted((auto | written) - set(sticker.owner_edited))
    patch["label_state"] = "done"
    patch["label_attempts"] = 0
    return patch
