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

# **触发判据**（v0.19.0）：回答"什么时候该想到它"，不是"怎么调"。
# 为什么单独一层：v0.17.0 把目录搬进常驻面、v0.18.0 把动作压成一次调用，实机仍然
# "不提醒她就不发"。同门那批她真会调的工具（forever_companion 的 mood_*）每一条都带
# 判据或跨工具路由（`mixins/mood_actions.py:638,667,695`），没有一条只摆选项——
# 因为它们的动作和我们要一次工具往返，而参考项目"只给许可"能成立是因为它写个标记就完事、
# 零往返。许可式措辞换到往返型动作上就是错配，这轮改的就是这个。
TRIGGER_CRITERIA: dict[str, str] = {
    "reserved": (
        "什么时候该想到它：你这句要是换成一张图会更准、更俏皮，才配；"
        "正经回答、报信息、办事的时候纯文字。"
    ),
    "natural": (
        "什么时候该想到它：对方话里有情绪或有反应空间（开心、无语、心疼、想撒娇、想吐槽）时值得配一张；"
        "纯信息问答、算东西、报时间的时候纯文字。"
    ),
    "eager": (
        "什么时候该想到它：对方一带情绪就配，不用等他点名要图；"
        "你已经连着几句只用文字了，也该主动想找一张。正经办事的时候还是纯文字。"
    ),
}

# 点名节奏（v0.19.0）：**积极度档位统一驱动三面**——判据措辞、注入点名句、几天几轮注一次。
# 数字是同门定案往两端推：eager 用 forever_companion 实机在跑的 3，natural 取中 6，reserved 12。
# 主人要"能自定义"：文件里把 `inject_interval_n` 写成具体数字就覆盖档位（0 = 跟随档位）。
INJECT_INTERVAL_BY_TIER: dict[str, int] = {"reserved": 12, "natural": 6, "eager": 3}
# 配置里这个值表示"跟随档位"，不是"每 0 轮"。
INJECT_INTERVAL_FOLLOW_TIER = 0


def effective_inject_interval_n(tier: str, configured: int) -> int:
    """这一档实际每几轮点名一次：配置写死优先，0/负数才跟随档位。

    不许拿档位去改发送层的任何一把尺（陷阱 26 没动）；这里只裁"提醒的密度"。
    """
    if isinstance(configured, int) and configured > 0:
        return configured
    return INJECT_INTERVAL_BY_TIER[normalize_tier(tier)]


# 返回值里的下一步指引（v0.19.0）。同门每个情绪工具成功后都用 `note` 指挥下一步
# （"被哄好时记得调用 mood_rising_tide"），我们以前只回 `{ok, sent, desc}`——
# 她做完一件事后拿不到任何"接下来呢"，那一面是白扔的。
# 这条**不提任何闸**（冷却/去重/概率在拒绝那一刻由 `_SEND_HINTS` 就地说，陷阱 29）。
NEXT_STEP_NOTE = "图已经发出去了，你不用再在文字里复述它。下一句有情绪想配就 sticker_send 填 group + text；没合适的就纯文字，别勉强。"


def injection_pointer(tier: str) -> str:
    """注入正文里点名那一句（v0.19.0）：说清"现在就可以调它"，而不是"去看说明"。"""
    return "现在这句有情绪想配，就调 sticker_send：填 group（分类名）和 text（你想说的那句）就一起发出去。"


def tool_criteria(tier: str) -> str:
    """工具描述里的判据段（和许可句一道，两个受众同一方向）。"""
    return TRIGGER_CRITERIA[normalize_tier(tier)]


def normalize_tier(tier: object) -> str:
    """不在册的档位退到默认档（与配置读入 `_as_choice` 同一条尺）。"""
    if isinstance(tier, str) and tier.strip() in EAGERNESS_LEVELS:
        return tier.strip()
    return EAGERNESS_DEFAULT


def injection_guidance(tier: str) -> str:
    """注入文案中段：意愿段 + 共用的节奏段。"""
    return f"{WILL[normalize_tier(tier)]}{RHYTHM}"


def send_tool_description(base: str, tier: str) -> str:
    """`sticker_send` 的完整描述：静态正文 + 这一档的触发判据 + 档位许可句。

    判据排在许可句之前：先回答"什么时候该想到它"，再说"这一档有多主动"——
    往返型动作上光给许可不够（v0.19.0 的实机教训，见 `TRIGGER_CRITERIA` 上方注释）。
    """
    normalized = normalize_tier(tier)
    return f"{base}{TRIGGER_CRITERIA[normalized]}\n{TOOL_NOTE[normalized]}"
