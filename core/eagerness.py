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
        "轻松接话、庆祝、贴贴、安慰、吐槽、犯困或晚安，贴切就优先配一张，不等要图，文字说清也别省掉；"
        "分清安慰与自述，不贴切或正经办事纯文字，单回复最多一张。"
    ),
}

# **决策检查点**（v0.20.0）：祈使句，要求的是"先做这个判断"，不是"必须发图"。
# 为什么换成祈使：宿主里唯一被验证她真会自主调用的工具（`recall_memory`）靠的不是工具
# 描述，是系统提示里那句「should call the recall_memory tool FIRST … even when she feels
# she already remembers」（宿主 `config/prompts/prompts_chara.py:121`）。陷阱 33 已经定性
# 许可式措辞在往返型动作上是错配，但 v0.19.0 只补到"判据"就停了——判据回答"什么时候该想
# 到她"，仍然没人要求她**每轮真的去想一次**。检查点补的就是这一下。
# 三条措辞纪律：① 落点必须在描述**最前**（她是先读任务再读选项的，判据在尾部只是附录）；
# ② 每一档都必须把"判断完是别配"写成合法出口，否则就是逼她滥发；
# ③ 不许出现禁令族措辞（陷阱 29 那族），闸的说明仍留在撞闸那一刻。
DECISION_CHECKPOINT: dict[str, str] = {
    "reserved": (
        "开口之前先做一次判断：这句配上图会不会比纯文字更准？判断不用配就直接用文字回。"
    ),
    "natural": (
        "开口之前先做一次判断：这句配上图会不会比纯文字更准、更俏皮？"
        "判断该配就调用本工具，别因为「文字已经说清楚了」就跳过这一步；"
        "判断不该配就直接用文字回，跳过是允许的。"
    ),
    "eager": (
        "每轮开口前先做这个判断：按自己要表达的态度找分类。"
        "轻松接话或情绪互动有贴切分类，就优先调用 sticker_send 配一张，不等用户要图，也别因文字已经说清楚就省掉。"
        "分清安慰对方和说自己；无贴切分类、信息问答或正经办事就纯文字，单回复最多一张。"
    ),
}

# 工具描述追加段（短）。同样不许写"干脆不发"这种退路——那是上一版 eager 失效的原因之一。
TOOL_NOTE: dict[str, str] = {
    "reserved": " 矜持档：只在非常贴切时配一张，多数回复纯文字就很好。",
    "natural": " 贴切就配一张，不贴切就别硬找。",
    "eager": (
        " 爱发档：有贴切分类的轻松互动优先配一张，不用等主人开口，文字说清也别省掉；"
        "按回复态度选 group，无贴切分类、信息问答或正经办事纯文字，单回复最多一张。"
    ),
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
        "什么时候该想到它：摸摸、贴贴、玩笑、轻松接话，或你想表达开心、无语、心疼、撒娇、吐槽时值得配一张；"
        "纯信息问答、算东西、报时间的时候纯文字。"
    ),
    "eager": (
        "什么时候该想到它：轻松接话、开心庆祝、撒娇贴贴、关心安慰、玩笑吐槽、犯困或道晚安，"
        "有贴切分类就优先调用 sticker_send 配一张，不用等用户点名要图，也别因文字已经说清楚就省掉。"
        "按自己回复要表达的态度选分类，分清安慰对方和说自己；"
        "无贴切分类、信息问答或正经办事就纯文字，单回复最多一张。"
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
NEXT_STEP_NOTE = (
    "表情包已提交到聊天，继续正常文字回复，不复述额外配文。"
    "后续互动想配图时仍可调用 sticker_send(group=分类名)，text 留空即可；没合适的就纯文字。"
)


def injection_pointer(tier: str) -> str:
    """注入正文里点名那一句（v0.19.0）：说清"现在就可以调它"，而不是"去看说明"。"""
    return (
        "回复有情绪或轻松互动时，按你想表达的态度选分类，调用 sticker_send(group=分类名)。"
        "正常文字回复照常说，text 留空即可，图片独立发送；没有合适的就纯文字。"
    )


def tool_criteria(tier: str) -> str:
    """工具描述里的判据段（和许可句一道，两个受众同一方向）。"""
    return TRIGGER_CRITERIA[normalize_tier(tier)]


def decision_checkpoint(tier: str) -> str:
    """工具描述的**第一句**：要求她每轮先做一次配图判断（v0.20.0）。

    与 `tool_criteria` 的分工：判据是"什么时候该想到它"的准绳，检查点是"这一轮必须
    真的想过一次"的动作指令。缺了后者，前者只是她不会去翻的附录。
    """
    return DECISION_CHECKPOINT[normalize_tier(tier)]


def normalize_tier(tier: object) -> str:
    """不在册的档位退到默认档（与配置读入 `_as_choice` 同一条尺）。"""
    if isinstance(tier, str) and tier.strip() in EAGERNESS_LEVELS:
        return tier.strip()
    return EAGERNESS_DEFAULT


def injection_guidance(tier: str) -> str:
    """Reminder willingness only; refusal guidance belongs to actual failed calls."""
    return WILL[normalize_tier(tier)]


def send_tool_description(base: str, tier: str) -> str:
    """`sticker_send` 的完整描述：静态正文 + 这一档的触发判据 + 档位许可句。

    判据排在许可句之前：先回答"什么时候该想到它"，再说"这一档有多主动"——
    往返型动作上光给许可不够（v0.19.0 的实机教训，见 `TRIGGER_CRITERIA` 上方注释）。
    """
    normalized = normalize_tier(tier)
    return f"{base}{TRIGGER_CRITERIA[normalized]}\n{TOOL_NOTE[normalized]}"
