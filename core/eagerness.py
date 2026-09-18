"""「配表情积极度」三档的语义（v0.15.0 收成一处的两个受众）。

档位只有一个含义——**她有多想配图**——但要喂给两个地方：

1. `injection_guidance(tier)`：存在感注入里的意愿段。低频、会随对话沉底；
2. `send_tool_description(base, tier)`：`sticker_send` 的工具描述。工具列表**每一轮都在场**，
   所以想让她"更想发"，这根杠杆比调注入频率有效得多（v0.15.0 的来由：实机日志显示
   6 次注入只换来 2 次发图，且两次都紧跟在注入后 30~60 秒内——三道闸一次都没拦）。

两段的**许可强度**必须同向（否则她读到的是自相矛盾的话），但**措辞分工**不同：
注入段可以长（讲道理），工具描述段必须短（工具描述是常驻提示，写长是在挤她的注意力）。

发送层的四把尺（冷却 / 最近不重复 / 概率闸 / 复用窗口）一律不吃档位——见 DESIGN 陷阱 26。
"""

from __future__ import annotations

from .configuration import EAGERNESS_DEFAULT, EAGERNESS_LEVELS

# 注入文案的意愿段（v0.14.0 起）。natural 段与 v0.13.0 那句一字不差——加档位不许顺带改默认行为。
WILL: dict[str, str] = {
    "reserved": (
        "发之前先想清楚这张图在回复什么：分清你是在安慰对方还是在说自己，"
        "拿不准、不贴切就不发；一条回复配一张就够，宁缺毋滥。"
        "多数时候纯文字就够了，图是点缀不是必需品——没有正合适的就别发。"
    ),
    "natural": (
        "发之前先想清楚这张图在回复什么：分清你是在安慰对方还是在说自己，"
        "拿不准、不贴切就不发；一条回复配一张就够，宁缺毋滥。"
    ),
    "eager": (
        "情绪对得上就配一张，别在心里过三遍才发——纯文字说多了显得生硬。"
        "一条回复一张就够，别连发；拿不准发哪张时带 group 让组内帮你选一张。"
    ),
}

# 节奏段是发送层的事实，三档共用：被拒了别重试。别按档改写，否则一调档连尺的说明都漂了。
RHYTHM = (
    "刚发过的会被'最近不重复'挡下，换一张就好；也有轮次会被节奏闸判定'这轮不配图'——"
    "被拒了就正常用文字回，别重试、也别换一张接着试。"
)

# 工具描述追加段（短）。同样不许写"干脆不发"这种退路——那是上一版 eager 失效的原因之一。
TOOL_NOTE: dict[str, str] = {
    "reserved": " 矜持档：只在非常贴切时配一张，多数回复纯文字就很好。",
    "natural": " 贴切就配一张，不贴切就别硬找。",
    "eager": " 爱发档：回复里情绪对得上就顺手配一张，不用等主人开口；拿不准用 group 让组内选。",
}


def normalize_tier(tier: object) -> str:
    """不在册的档位退到默认档（与配置读入 `_as_choice` 同一条尺）。"""
    if isinstance(tier, str) and tier.strip() in EAGERNESS_LEVELS:
        return tier.strip()
    return EAGERNESS_DEFAULT


def injection_guidance(tier: str) -> str:
    """注入文案中段：意愿段 + 共用的节奏段。"""
    return f"{WILL[normalize_tier(tier)]}{RHYTHM}"


def send_tool_description(base: str, tier: str) -> str:
    """`sticker_send` 的完整描述：静态正文 + 档位的许可句。"""
    return f"{base}{TOOL_NOTE[normalize_tier(tier)]}"
