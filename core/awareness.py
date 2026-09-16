"""存在感注入（v0.2.0）的纯函数层：选"注什么"，拼"注成什么样"。

**为什么需要它**：`sticker_list` / `sticker_send` 两个工具一直在，但它们考的是
"她想不想得起来用"。参考项目的做法是每轮把分类目录喂进上下文——那是平台钩子，
插件拿不到；插件侧的等价通道是 `push_message(visibility=[], ai_behavior="read")`
的静默注入（our_life 的 injector 已验证这条在 timer 里可走）。本模块只管两件事：

1. **挑内容**：库不为空才有存在感；带的是"最近常用"的前 N 张（她最常甩出去的那几张
   最能唤起"哦我还有这个"），不是全目录——低频提示不是目录复读机。
2. **拼文案**：给模型看的中文（our_life 同一纪律：面向模型的注入文本不走 i18n，
   面板/入口的用户文案才走）。行形状复用 `format_catalog_for_model`，
   她从这里看到的行与 `sticker_list` 返回的完全对偶——两处必须同一把尺。

节奏（间隔多少秒、注入给谁）不在这层：那是 services/awareness.py 与时钟的事。
"""

from __future__ import annotations

from .catalog import Sticker, format_catalog_for_model, format_group_overview

# 注入文本的骨架。刻意不提"系统提示"这类元话语，也不下命令——
# 她是自愿用表情的主人，不是被执行分支的脚本；给的是"有什么 + 在哪查 + 怎么发"。
# 轮 C 起中段多一条**使用规则**（学习外部系统 提示词里"区分安慰与自述、不贴切就不发"
# 与数量软提示）：硬闸在发送层的冷却，软提示进这段文案——两层分离，各管各的。
_HEADER = "【表情包】你的收藏间里有 {count} 张表情包。"
_GUIDANCE = (
    "发之前先想清楚这张图在回复什么：分清你是在安慰对方还是在说自己，"
    "拿不准、不贴切就不发；一条回复配一张就够，宁缺毋滥。"
    "刚发过的会被'最近不重复'挡下，换一张就好；也有轮次会被节奏闸判定'这轮不配图'——"
    "被拒了就正常用文字回，别重试、也别换一张接着试。"
)
_RECENT = "最近常用的：\n{lines}"
_GROUPS = "你的套图（挑组再挑图，也可直接用 sticker_send 带 group 让组内帮你选）：\n{overview}"
_FOOTER = (
    "聊天里想配张图就用 sticker_send 发（给关键词会帮你筛，候选不止一个会回列表让你挑 id；"
    "先 sticker_list 可以看全部）。别硬找、别连发；主人点名要再看某张时，用 force 绕行。"
)


def pick_recent(stickers: list[Sticker], limit: int) -> list[Sticker]:
    """可选面（未禁用）里按"最近爱用"挑前 N 张：使用数 > 最近时刻 > 更早入库。

    排序口径与 `search_stickers` 空查询一致——同一个"常货"定义在两处出现，
    所以直接走同一条 key（对偶纪律）。
    """
    pool = [s for s in stickers if not s.disabled]
    ranked = sorted(pool, key=lambda s: (-s.use_count, -s.last_used_at, s.added_at))
    return ranked[: max(0, limit)]


def build_awareness_text(stickers: list[Sticker], *, max_lines: int, groups: dict[str, str] | None = None) -> str:
    """拼一条注入文本。空库回空串——调用方拿空串当"这拍不该注"。

    轮 F：分组概览插在指南之后、常货之前——她的"有什么"心智先从逐图清单升一层到
    分类目录（对齐外部系统每轮喂分类行的体验，只是我们靠低频静默注入）。
    """
    total = sum(1 for s in stickers if not s.disabled)
    if total <= 0:
        return ""
    parts = [_HEADER.format(count=total), _GUIDANCE]
    overview = format_group_overview(stickers, groups or {})
    if overview:
        parts.append(_GROUPS.format(overview=overview))
    lines = format_catalog_for_model(pick_recent(stickers, max_lines), max_lines, groups)
    if lines:
        parts.append(_RECENT.format(lines=lines))
    parts.append(_FOOTER)
    return "\n".join(parts)
