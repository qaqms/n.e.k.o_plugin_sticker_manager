"""`sticker_send` 的常驻面（v0.17.0）：能力句 + 许可 + 激活区的分类全表。

**为什么目录挂在这里而不是挂在注入里**：工具列表每一轮都随请求喂给模型，而且插件
重挂一次就推到活着的会话上（宿主 `main_logic/core/tool_calling.py:67` 的
`register_tool_and_sync` → `_sync_tools_to_active_session` → realtime 的
`session.update {tools}`；唯一例外是 Gemini 方言不支持中途换工具表）。
`push_message(ai_behavior="read")` 不是这一类东西：它是**排干即弃的一次性 cue**——
文字模式等下一个用户话轮，语音模式要等下一次自然热切换（宿主
`main_logic/core/lifecycle.py:2749-2778` 明写"哪怕那隔好几个话轮"，且是 owner 决定）。
实机账本对得上：10 次注入只换 2 次发图。

**参考侧的机制**（本轮搬的是它频率高的前两条，第三条"标记协议"插件碰不到她的
输出流所以搬不成）：① 每轮把 `分类键 - 用途` 全表缝进常驻面；② 那上面**只有许可、
没有闸**——概率 100%、条数不限，全部做成模型看不见的事后过滤。它整套提示词里找不到
一句"你必须配图"。所以这里也不写祈使句，闸一律留在 `services/sender.py` 硬拦。

纪律：本模块是纯函数，输入只有"分类概览串 + 档位"，不碰库也不碰 SDK；
档位许可句仍然只有 `core/eagerness.py` 一个来源（陷阱 26），这里调它而不是抄它。
"""

from __future__ import annotations

from .catalog import Sticker, format_group_overview
from .eagerness import decision_checkpoint, normalize_tier, send_tool_description

# 能力句：说清"这是什么"，并把三种形态都摆成合法选项（参考侧 prompt.head 的结构）。
_HEAD = "发一张你收藏间里的表情包，配合你正在回的这句话。纯文字、正常回复配图、只发图都行。"

# 读表指引 + 最低摩擦路径。刻意不提冷却/去重/概率这些闸：她们在拒绝的那一刻
# 由 `_SEND_HINTS` 就地说明就够了，常驻面上写禁令只会教她别发。
# 普通回复留在她自己的回复通道；可选 text 仅作额外配文，与图片分条，署名仍是插件。
_TAIL = (
    "日常只需 sticker_send(group=分类名)，从下方目录选分类，组内帮你选图，无需先查列表。"
    "指定某张才填 sticker_id，关键词检索才填 query。"
    "正常文字回复照常说，text 留空即可，图片独立发送；"
    "只有额外配文才填 text，配文与图片分条发送，配文不要在正文重复。"
)

# 空库/空区时也得有一句实话——没有可发的分类还鼓励她发，只会换来一堆失败调用。
_EMPTY = "（这个区暂时还没有可发的分类，先纯文字回，等主人往里收图。）"

CATALOG_TITLE = "【你的分类】"

# 「未分组」是我们面板侧的记账词，不是她能填进 group 的值（填了必回 group_not_found）。
# 概览尺会带上这一行，所以进常驻面前先摘掉。
_UNGROUPED_MARK = "・未分组"


def catalog_for_tool(stickers: list[Sticker], groups: dict[str, str] | None = None) -> str:
    """激活区 → 分类概览串（每行一个分类）。

    尺复用 `format_group_overview`：只有**有未禁用的图**的分类才出——空分类对她
    隐形（陷阱 21），否则她选中一个空分类就是目录里有它、一发回 `group_not_found`
    的鬼打墙。
    """
    overview = format_group_overview(stickers, groups or {})
    lines = [line for line in overview.splitlines() if not line.startswith(_UNGROUPED_MARK)]
    return "\n".join(lines)


def build_send_tool_description(catalog: str, tier: str = "natural") -> str:
    """拼 `sticker_send` 的完整工具描述：决策检查点 + 能力句 + 读表指引 + 分类全表 + 档位许可句。

    检查点排最前（v0.20.0，陷阱 34）：判据与许可句都在尾部，对往返型动作推不动她——
    宿主里她真会自主调用的那条先例（`recall_memory`）靠的就是系统提示里一句"先调用它"。

    档位那句仍由 `core/eagerness.send_tool_description` 追加——两个受众共用一个来源。
    """
    body = f"\n{CATALOG_TITLE}\n{catalog}\n" if catalog else f"\n{_EMPTY}\n"
    base = f"{decision_checkpoint(tier)}\n{_HEAD}\n{_TAIL}{body}"
    return send_tool_description(base, normalize_tier(tier))
