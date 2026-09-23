"""存在感注入（v0.2.0）的纯函数层：选"注什么"，拼"注成什么样"。

**为什么需要它**：`sticker_list` / `sticker_send` 两个工具一直在，但它们考的是
"她想不想得起来用"。注入就是那句"想起来"。

**v0.17.0 把职责收窄了**：目录（分类全表）不再由注入带——它搬进了 `sticker_send` 的
工具描述（`core/tool_surface.py`），那里每轮都在场；注入是排干即弃的一次性 cue，
当目录载体不合格。本模块现在只管两件事：

1. **挑时机**：`injection_due_for_turn` 把"新一轮到了"翻成"这轮注不注"（纯判定）。
2. **拼文案**：库大小 + 指向常驻面的一句 + 按档位的意愿段。空库回空串=不该注。
   给模型看的中文不走 i18n（our_life 同一纪律），面板/入口的用户文案才走。

节奏（间隔多少秒、注入给谁）不在这层：那是 services/awareness.py 与时钟的事。
"""

from __future__ import annotations

from typing import Any

from .catalog import Sticker
from .configuration import INJECT_MODE_DEFAULT, INJECT_MODES
from .eagerness import injection_guidance, injection_pointer


def normalize_mode(mode: Any) -> str:
    """不在册的档位退到默认——与 eagerness 档位同一条尺：宁可退档也不炸拍。"""
    value = str(mode or "").strip()
    return value if value in INJECT_MODES else INJECT_MODE_DEFAULT


def injection_due_for_turn(
    mode: Any,
    *,
    turns_since_inject: int,
    interval_n: int,
    floor_remaining_sec: float,
) -> bool:
    """新一轮到了，这一轮注不注（纯判定，时钟与计数都在调用方手里）。

    地板未过一律 False——`min_interval_sec` 是连珠炮防刷屏用的，两种模式都吃它。
    计数只在注入成功后清零（见 services/turns.py 纪律），所以这里被闸拦下时
    不消耗计数：攒够 N 轮而这轮被地板挡住时，下一轮仍然立刻命中。
    """
    if floor_remaining_sec > 0.0:
        return False
    if normalize_mode(mode) == "every_user_message":
        return True
    return turns_since_inject >= max(1, int(interval_n))


# 注入文本的骨架。刻意不提"系统提示"这类元话语，也不下命令——
# 她是自愿用表情的主人，不是被执行分支的脚本；给的是"有什么 + 在哪查 + 怎么发"。
# 轮 C 起中段多一条**使用规则**（学习外部系统 提示词里"区分安慰与自述、不贴切就不发"
# 与数量软提示）：硬闸在发送层的冷却，软提示进这段文案——两层分离，各管各的。
_HEADER = "【表情包】你的收藏间里有 {count} 张表情包。"
# v0.17.0：**目录与常货行从注入里退场**。它们搬去了常驻面（`core/tool_surface.py`
# 拼进 `sticker_send` 的工具描述，每轮都在场），注入只剩"想起来"这一件事。
# 理由不是省字——是载体错了：`ai_behavior="read"` 是排干即弃的一次性 cue，文字模式
# 等下一个用户话轮、语音模式要等下一次自然热切换（宿主 lifecycle.py 明写"哪怕隔好几个
# 话轮"）。把目录写进这种 cue，等于把地图塞进一张会过期的便条。
# 意愿段与节奏段都在 `core/eagerness.py`（v0.15.0 收口）：那里同时管"注入怎么说"和
# "工具描述怎么说"，两处许可强度必须同向，所以不许在这边再抄一份。
# v0.19.0：这句从"去看说明"改成**点名工具 + 说清现在就能做**。同门的经验是三面叠加
# （描述给判据 + 事件门控的点名提醒 + 返回值指挥下一步），只靠常驻面那一条推不动她
# ——见 `core/eagerness.TRIGGER_CRITERIA` 上方注释。尺在 `core/eagerness.injection_pointer`。
def build_awareness_text(
    stickers: list[Sticker],
    *,
    eagerness: str = "natural",
) -> str:
    """拼一条注入文本。空库回空串——调用方拿空串当"这拍不该注"。

    v0.17.0 起它只有三句：库有多大 + 点名怎么做 + 按档位的意愿/节奏。
    轮 F 那版还会附套图分类概览与最近常用前 N 行——那是把一次性 cue 当目录载体用，
    实机账本（10 次注入 / 2 次发图）之后换成了常驻面。
    v0.14.0：意愿段按「配表情积极度」选档，不在册的档位退到 natural（与配置读入同一条尺）。
    """
    total = sum(1 for s in stickers if not s.disabled)
    if total <= 0:
        return ""
    return "\n".join(
        [_HEADER.format(count=total), injection_pointer(eagerness), injection_guidance(eagerness)]
    )
