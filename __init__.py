"""表情包管理 (sticker_manager) —— 让她收藏、送出自己的表情包。

架构（一段话）：`core/` 是零 SDK 依赖的纯函数层（条目形状 / 格式嗅探 / 检索 /
目录文案 / 配置视图），`services/` 是有状态层（`data/` 下的 JSON 目录 + 图片文件、
发送链路与冷却台账），本模块只做装配与对外契约面（entry / ui.context / ui.action /
llm_tool）。

v0.1.0「能收能发」：主人在面板里收藏表情包（上传、写描述、打标签、禁用、删除），
她通过 `sticker_list` / `sticker_send` 两个工具在对话里自主挑一张发出去；
发送走 `push_message` 的 image part（≤ 内联预算直发原字节，大图换 URL part），
每次成败都记进使用台账，面板能看到"她最近爱用什么"。

v0.1.4「不再静默缺席」：工具注册心跳（services/tool_watch）——main_server 重启或
晚于插件启动时，她的 `sticker_list` / `sticker_send` 会静默缺席，巡检器每 5 分钟
点名补挂。

v0.2.0「她得记得自己有表情」：三件事——① 存在感注入（services/awareness，挂在
60s watch 拍上的低频静默提示，治"她想不起来有表情"）；② 套图分组（Sticker.group，
检索/目录/面板全贯通）；③ 库的导出与套图包导入（manifest + zip，收件箱认包）。

三条硬约束（源码核实，见 DESIGN.md「已知陷阱」）：
1. `ctx.images.upload()` 会把图**归一成 JPEG**——gif 动图会被压平，所以动图只走
   内联通道，内联预算装不下就如实拒绝，不上报假成功。
2. 角色归属只取本次调用注入的 `_ctx["lanlan_name"]`，绝不读 `ctx._current_lanlan`
   （脏值会把 A 角色的发送记到 B 角色头上）。
3. `push_message` 的 `submitted=True` 只代表已交给传输；发送冷却只在内存里，
   重启即清零（刻意，见 services/sender 模块 docstring）。
"""

from __future__ import annotations

import base64
import binascii
import random
import time
from typing import Any

from plugin.sdk.plugin import (  # pyright: ignore[reportMissingImports] — 独立仓无宿主包树；挂载态可解析，测试由 conftest 桩接管
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    timer_interval,
    tr,
    ui,
)

from .core import (
    CAPTION_MAX_CHARS,
    GROUP_DESC_MAX_CHARS,
    MAX_STICKER_BYTES,
    PREVIEW_CHUNK_BYTES,
    UPLOAD_CHUNK_BYTES,
    VISIBLE_TEXT_MAX_CHARS,
    Sticker,
    StickerManagerSettings,
    format_catalog_for_model,
    format_group_overview,
    normalize_group,
    normalize_tags,
    parse_tags_field,
    resolve_send_target,
    search_stickers,
    validate_desc,
    validate_desc_optional,
    validate_optional_text,
)
from .services import Awareness, Library, Sender, ToolWatch

__all__ = ["StickerManagerPlugin"]

# 上传解码前的字节上限提示（真正的 8 MiB 判定在 add 入口）。
_MAX_BASE64_CHARS = 12 * 1024 * 1024
# 直传单块的 base64 尺寸上限：3 MiB 原始恰好多不出 4 MiB 字符；给 JSON 封套留余量，
# 超过就是故意的帧溢出尝试，在解码前拦下（同预览方向的 4,784,128 字节尺）。
_UPLOAD_B64_MAX_CHARS = 4 * 1024 * 1024


# 工具层拒发的二段指引（面向模型，同 multi_candidates 的 hint 一样走硬编码中文：
# 消费方是模型不是面板，不走 i18n；reason 码本身才是给面板/日志的稳态契约）。
_SEND_HINTS: dict[str, str] = {
    "recent_repeat": "这张最近发过了——换一张或干脆用文字回；只有主人点名要再看这张时才 force=true 重发。",
    "probability_declined": "这一轮不配图：直接用文字回复，别重试、也别换一张再试。",
    "send_cooldown": "刚发过一张，她在冷却：这轮用文字回复。",
}

# 组内选图的随机尺（轮 F，对齐外部系统“模型只挑分类、组内随机”）：
# 模块级可注入——测试里换掉即可钉死“选的是哪张”，生产就是 random.choice。
_GROUP_PICK = random.choice


def _clean_id_list(value: Any, *, cap: int = 200) -> list[str]:
    """批量入口的 ids 参数尺：只收非空字符串、去重保序、限量。"""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        sid = item.strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
        if len(out) >= cap:
            break
    return out


def _match_group_names(names: list[str], term: str) -> list[str]:
    """组名解析：精确 > 子串（casefold）；多命中全回，让工具面决定“拍板还是回列表”。"""
    if term in names:
        return [term]
    folded = term.casefold()
    return [n for n in names if folded in n.casefold()]


@neko_plugin
class StickerManagerPlugin(NekoPluginBase):
    """表情包收藏与发送：面板管理库，她在对话里自主发图。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._settings = StickerManagerSettings.defaults()
        self._library = Library(self.data_path("library"), logger=self.logger)
        self._sender = Sender(self, self._library, logger=self.logger)
        # 工具注册心跳（v0.1.4）：@llm_tool 只在启动时发一次 IPC，main_server 没就绪
        # 或重启后她的两个工具会静默缺席（见 services/tool_watch.py 模块 docstring）。
        # 与总开关无关：注册韧性是宿主层面的在场性，不随业务冻结而应冻结。
        self._tool_watch = ToolWatch(self, logger=self.logger)
        # 存在感注入（v0.2.0）：与 tool_watch 共用 60s 拍，内部按角色卡时钟自节流。
        # 与总开关是「与」关系——[sticker_manager].enabled=false 时不注。
        self._awareness = Awareness(self, self._library, logger=self.logger)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        await self._reload_settings()
        loaded = self._library.load(force=True)
        self.logger.info(
            "sticker_manager ready: enabled={} stickers={} io_ok={}",
            self._settings.enabled,
            self._library.count(),
            loaded.ok,
        )
        return Ok(
            {
                "status": "ready",
                "enabled": self._settings.enabled,
                "stickers": self._library.count(),
            }
        )

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        await self._reload_settings()
        return Ok({"status": "config_updated", "enabled": self._settings.enabled})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        # 本插件的写路径全部是同步落盘（写穿式 JSON + 原子替换），没有待 flush 的
        # 内存态；shutdown 只留一个收尾日志与"目录还能读"的健康探针。
        probe = self._library.load(force=True)
        self.logger.info(
            "sticker_manager stopped: stickers={} catalog_ok={}",
            self._library.count(),
            probe.ok,
        )
        return Ok({"status": "stopped", "stickers": self._library.count()})

    # ------------------------------------------------------------------
    # 工具注册心跳（v0.1.4）
    # ------------------------------------------------------------------

    @timer_interval(id="watch", seconds=60, name="sticker_manager watch")
    async def on_watch(self, **_):
        # 每 60s 递一下心跳器与注入器，各自内部按更长间隔自节流。
        # timer 每拍跑在新 event loop 且无 watchdog：异常必须自己兜住（陷阱 §3）。
        # 两件事各兜各的：心跳坏不许拖累注入，注入坏不许拖累心跳（两表都黄才是双灾）。
        try:
            watch = await self._tool_watch.maybe_run(now=time.time())
        except Exception:  # noqa: BLE001 - timer 无 watchdog，异常漏出去会停不了但也静默
            self.logger.warning("sticker_manager tool watch leaked", exc_info=True)
            watch = {"status": "leaked"}
        try:
            awareness = await self._awareness.maybe_run(settings=self._settings, now=time.time())
        except Exception:  # noqa: BLE001 - 注入坏掉不许把表标黄、更不许拖累心跳
            self.logger.warning("sticker_manager awareness leaked", exc_info=True)
            awareness = {"status": "leaked"}
        return Ok({"tool_watch": watch, "awareness": awareness})

    # ------------------------------------------------------------------
    # 管理入口（面板 + 命令面板共用）
    # ------------------------------------------------------------------

    @ui.action(
        id="add",
        label=tr("actions.add.label", default="Add"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="add",
        name=tr("entries.add.name", default="收藏一张表情包"),
        description=tr(
            "entries.add.description",
            default="把一张图片存进表情库：data_base64 是图片本体（不含 data: 前缀）。轮 F 起逐图文本全部可选——"
            "不写也能收，她靠分组说明选图；caption（梗义）仍是选图最准的一把尺，顺手就写",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "data_base64": {
                    "type": "string",
                    "description": tr("fields.data_base64", default="图片 base64（png/jpg/gif/webp，≤8MiB）"),
                },
                "desc": {
                    "type": "string",
                    "description": tr("fields.desc", default="一句话描述图里在干什么（可选，≤200字）"),
                },
                "tags": {
                    "type": "string",
                    "description": tr("fields.tags", default="标签，逗号分隔（可选）"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，如：猫猫日常）"),
                },
                "caption": {
                    "type": "string",
                    "description": tr(
                        "fields.caption",
                        default="梗义：这张图在回复什么、什么上一句会触发发它（可选，≤300字）",
                    ),
                },
                "visible_text": {
                    "type": "string",
                    "description": tr("fields.visible_text", default="图里清晰可见的原文字（可选，≤200字）"),
                },
            },
            "required": ["data_base64"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "id", "desc"],
        timeout=30.0,
    )
    async def add_entry(
        self,
        data_base64: str = "",
        desc: str = "",
        tags: str = "",
        group: str = "",
        caption: str = "",
        visible_text: str = "",
        **_,
    ):
        # 轮 F：desc 不再是入库门槛（学外部系统：逐图零文本也能用）；只拦超限。
        text_desc, desc_error = validate_desc_optional(desc)
        if desc_error:
            return Err(SdkError("desc_too_long"))
        text_caption, caption_error = validate_optional_text(caption, limit=CAPTION_MAX_CHARS)
        if caption_error:
            return Err(SdkError("caption_too_long"))
        text_visible, visible_error = validate_optional_text(visible_text, limit=VISIBLE_TEXT_MAX_CHARS)
        if visible_error:
            return Err(SdkError("visible_text_too_long"))
        if not isinstance(data_base64, str) or not data_base64:
            return Err(SdkError("image_required"))
        if len(data_base64) > _MAX_BASE64_CHARS:
            return Err(SdkError("image_too_large"))
        try:
            payload = base64.b64decode(data_base64, validate=False)
        except (binascii.Error, ValueError):
            return Err(SdkError("image_undecodable"))
        if len(payload) > MAX_STICKER_BYTES:
            return Err(SdkError("image_too_large"))
        sticker, error = self._library.add(
            data=payload,
            desc=text_desc,
            tags=parse_tags_field(tags),
            group=normalize_group(group),
            caption=text_caption,
            visible_text=text_visible,
            now=time.time(),
        )
        if sticker is None:
            return Err(SdkError(error or "image_rejected"))
        return Ok({"note": "sticker_added", "id": sticker.id, "desc": sticker.desc})

    @ui.action(
        id="update",
        label=tr("actions.update.label", default="Edit"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="update",
        name=tr("entries.update.name", default="修改表情包"),
        description=tr(
            "entries.update.description",
            default="改描述/梗义/图内文字/标签/禁用状态。梗义决定她会不会选中这张图（空串=清除标注），禁用=从她的可选面里摘掉",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": tr("fields.id", default="表情包 id")},
                "desc": {"type": "string", "description": tr("fields.desc", default="新描述（留空=不改）")},
                "tags": {"type": "string", "description": tr("fields.tags", default="新标签（留空=不改）")},
                "disabled": {
                    "type": "boolean",
                    "description": tr("fields.disabled", default="是否禁用（缺省=不改）"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，如：猫猫日常）"),
                },
                "caption": {
                    "type": "string",
                    "description": tr(
                        "fields.caption",
                        default="梗义：这张图在回复什么、什么上一句会触发发它（可选，≤300字）",
                    ),
                },
                "visible_text": {
                    "type": "string",
                    "description": tr("fields.visible_text", default="图里清晰可见的原文字（可选，≤200字）"),
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "id"],
    )
    async def update_entry(
        self,
        id: str = "",  # noqa: A002 - 入口参数名就是 id（面板与模型看到的形状）
        desc: str | None = None,
        tags: Any = None,
        disabled: bool | None = None,
        group: Any = None,
        caption: Any = None,
        visible_text: Any = None,
        **_,
    ):
        if not isinstance(id, str) or not id:
            return Err(SdkError("sticker_not_found"))
        new_desc: str | None = None
        if isinstance(desc, str):
            # 轮 F：空串是合法意图（清掉逐图描述，靠分组说明顶上）；只有 None（缺席）才是不改。
            if not desc.strip():
                new_desc = ""
            else:
                text_desc, desc_error = validate_desc(desc)
                if desc_error:
                    return Err(SdkError(f"desc_{desc_error}"))
                new_desc = text_desc
        new_tags: list[str] | None = None
        if tags is not None and not (isinstance(tags, str) and not tags.strip()):
            new_tags = parse_tags_field(tags)
        if disabled is not None and not isinstance(disabled, bool):
            return Err(SdkError("invalid_value"))
        # group/caption/visible_text 与 tags 的语义不同：**空串是合法意图**（"移出分组"/"清掉标注"），
        # 只有 None（参数缺席）才表示"不改"。
        new_group: str | None = None
        if isinstance(group, str):
            new_group = normalize_group(group)
        new_caption: str | None = None
        if isinstance(caption, str):
            new_caption, caption_error = validate_optional_text(caption, limit=CAPTION_MAX_CHARS)
            if caption_error:
                return Err(SdkError("caption_too_long"))
        new_visible: str | None = None
        if isinstance(visible_text, str):
            new_visible, visible_error = validate_optional_text(visible_text, limit=VISIBLE_TEXT_MAX_CHARS)
            if visible_error:
                return Err(SdkError("visible_text_too_long"))
        sticker, error = self._library.update(
            id,
            desc=new_desc,
            tags=new_tags,
            disabled=disabled,
            group=new_group,
            caption=new_caption,
            visible_text=new_visible,
        )
        if sticker is None:
            return Err(SdkError(error or "sticker_not_found"))
        return Ok({"note": "sticker_updated", "id": sticker.id})

    @ui.action(
        id="remove",
        label=tr("actions.remove.label", default="Delete"),
        tone="danger",
        refresh_context=True,
        confirm=True,
    )
    @plugin_entry(
        id="remove",
        name=tr("entries.remove.name", default="删除表情包"),
        description=tr("entries.remove.description", default="从库里删掉这张图和它的记录（不可恢复）"),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": tr("fields.id", default="表情包 id")},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "id"],
    )
    async def remove_entry(self, id: str = "", **_):  # noqa: A002
        if not isinstance(id, str) or not id:
            return Err(SdkError("sticker_not_found"))
        error = self._library.remove(id)
        if error:
            return Err(SdkError(error))
        return Ok({"note": "sticker_removed", "id": id})

    # ------------------------------------------------------------------
    # 轮 F：分组说明与批量整理（对齐外部系统“描述挂分类、整理靠批量”的管理面）
    # ------------------------------------------------------------------

    @ui.action(
        id="group_set_desc",
        label=tr("actions.group_set_desc.label", default="Group note"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="group_set_desc",
        name=tr("entries.group_set_desc.name", default="给套图分类写一句说明"),
        description=tr(
            "entries.group_set_desc.description",
            default="分类说明是她选图时看到的“分类目录”正文（一句“什么时候用这一组”，≤300字）；空串=清掉这句话（分类本身不删，删分类用 group_remove）",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": tr("fields.group_name", default="分类名（须已在册：建过分类或有图在用）"),
                },
                "desc": {
                    "type": "string",
                    "description": tr("fields.group_desc", default="什么时候用这一组（≤300字）"),
                },
            },
            "required": ["group"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "group"],
    )
    async def group_set_desc_entry(self, group: str = "", desc: str = "", **_):
        name = normalize_group(group)
        if not name:
            return Err(SdkError("group_required"))
        text, error = validate_optional_text(desc, limit=GROUP_DESC_MAX_CHARS)
        if error:
            return Err(SdkError("group_desc_too_long"))
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        known = self._library.group_names()
        if name not in known:
            # 只能给“在册”的分类写说明。轮 I 之后分类可以先建后用（group_create），
            # 但依旧不认“什么都没见过”的名字：打错一个字不许静默造出一个新分区。
            return Err(SdkError("group_not_found"))
        ok, error = self._library.set_group_desc(name, text)
        if not ok:
            return Err(SdkError(error or "group_io_error"))
        return Ok({"note": "group_desc_set", "group": name, "cleared": not text})

    # ---------------------------------------------------------------
    # 轮 I：分类优先的管理面（先立分类 → 往分类里收图 → 整间拆掉）
    # ---------------------------------------------------------------

    @ui.action(
        id="group_create",
        label=tr("actions.group_create.label", default="New category"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="group_create",
        name=tr("entries.group_create.name", default="新建一个表情分类"),
        description=tr(
            "entries.group_create.description",
            default="先立分类再收图：名字必填，说明可留空（她选图时看到的分类正文就是这句话）。空分类对她是不可见的：不进目录、不能被选去发",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "group": {"type": "string", "description": tr("fields.group_name_new", default="分类名字")},
                "desc": {
                    "type": "string",
                    "description": tr("fields.group_desc", default="什么时候用这一组（≤300字）"),
                },
            },
            "required": ["group"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "group"],
    )
    async def group_create_entry(self, group: str = "", desc: str = "", **_):
        name = normalize_group(group)
        if not name:
            return Err(SdkError("group_required"))
        text, error = validate_optional_text(desc, limit=GROUP_DESC_MAX_CHARS)
        if error:
            return Err(SdkError("group_desc_too_long"))
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        ok, create_error = self._library.create_group(name, text)
        if not ok:
            return Err(SdkError(create_error or "group_io_error"))
        return Ok({"note": "group_created", "group": name})

    @ui.action(
        id="group_remove",
        label=tr("actions.group_remove.label", default="Delete category"),
        tone="danger",
        refresh_context=True,
    )
    @plugin_entry(
        id="group_remove",
        name=tr("entries.group_remove.name", default="删掉一个分类（连带删它里的图）"),
        description=tr(
            "entries.group_remove.description",
            default="拆掉整个分区：这个分类下的每一张图与它们的文件一起删，不可恢复（面板先把精确张数摊开再问）",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": tr("fields.group_name", default="分类名（须已在册：建过分类或有图在用）"),
                },
            },
            "required": ["group"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "group", "removed"],
        # 陷阱 19 那两把尺：面板长任务走 LONG_CALL=120s，服务端同尺寸，
        # 否则一个大分类（几百张）删到一半先被服务端抢断，面板报“操作失败”而盘上已删。
        timeout=120.0,
    )
    async def group_remove_entry(self, group: str = "", **_):
        name = normalize_group(group)
        if not name:
            return Err(SdkError("group_required"))
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        ok, error, removed = self._library.remove_group(name)
        if not ok:
            return Err(SdkError(error or "group_io_error"))
        return Ok({"note": "group_removed", "group": name, "removed": removed})

    @ui.action(
        id="batch_update",
        label=tr("actions.batch_update.label", default="Batch edit"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="batch_update",
        name=tr("entries.batch_update.name", default="批量编辑一批表情包"),
        description=tr(
            "entries.batch_update.description",
            default="对一批 id 统一：加标签/删标签/移到分组/启停。只改传来的项，缺席不改；逐张走同一把 update 尺",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": tr("fields.ids", default="表情 id 列表（≤200）"),
                },
                "tags_add": {"type": "string", "description": tr("fields.tags_add", default="要加的标签，逗号分隔")},
                "tags_remove": {
                    "type": "string",
                    "description": tr("fields.tags_remove", default="要删的标签，逗号分隔"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，整批共用；包里自带的优先）"),
                },
                "disabled": {"type": "boolean", "description": tr("fields.disabled", default="禁用/启用")},
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "updated"],
    )
    async def batch_update_entry(
        self,
        ids: Any = None,
        tags_add: Any = None,
        tags_remove: Any = None,
        group: Any = None,
        disabled: Any = None,
        **_,
    ):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        id_list = _clean_id_list(ids)
        if not id_list:
            return Err(SdkError("batch_empty"))
        add_tags = parse_tags_field(tags_add) if isinstance(tags_add, (str, list)) else None
        remove_tags = (
            {t.casefold() for t in parse_tags_field(tags_remove)} if isinstance(tags_remove, (str, list)) else set()
        )
        new_group = normalize_group(group) if isinstance(group, str) else None
        if add_tags is None and not remove_tags and new_group is None and disabled is None:
            return Err(SdkError("batch_noop"))
        if disabled is not None and not isinstance(disabled, bool):
            return Err(SdkError("invalid_value"))
        updated = 0
        missing: list[str] = []
        for sid in id_list:
            sticker = self._library.get(sid)
            if sticker is None:
                missing.append(sid)
                continue
            merged_tags: list[str] | None = None
            if add_tags is not None or remove_tags:
                merged = [t for t in sticker.tags if t.casefold() not in remove_tags]
                for tag in add_tags or []:
                    if tag.casefold() not in {m.casefold() for m in merged}:
                        merged.append(tag)
                merged_tags = normalize_tags(merged)
            result, error = self._library.update(
                sid, tags=merged_tags, group=new_group, disabled=disabled if isinstance(disabled, bool) else None
            )
            if result is None:
                missing.append(sid if sid else (error or "?"))
            else:
                updated += 1
        return Ok({"note": "library_batch_updated", "updated": updated, "missing": missing})

    @ui.action(
        id="batch_remove",
        label=tr("actions.batch_remove.label", default="Batch delete"),
        tone="danger",
        refresh_context=True,
        confirm=True,
    )
    @plugin_entry(
        id="batch_remove",
        name=tr("entries.batch_remove.name", default="批量删除表情包"),
        description=tr(
            "entries.batch_remove.description",
            default="按 id 列表逐张删除（图与记录，不可恢复）；面板侧负责先把精确张数摊在确认里",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": tr("fields.ids", default="表情 id 列表（≤200）"),
                },
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "removed"],
    )
    async def batch_remove_entry(self, ids: Any = None, **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        id_list = _clean_id_list(ids)
        if not id_list:
            return Err(SdkError("batch_empty"))
        removed = 0
        missing: list[str] = []
        for sid in id_list:
            error = self._library.remove(sid)
            if error:
                missing.append(sid)
            else:
                removed += 1
        return Ok({"note": "library_batch_removed", "removed": removed, "missing": missing})

    @ui.action(
        id="send",
        label=tr("actions.send.label", default="Send"),
        tone="success",
        refresh_context=True,
    )
    @plugin_entry(
        id="send",
        name=tr("entries.send.name", default="把这张表情发到聊天"),
        description=tr("entries.send.description", default="面板上的试发按钮：以当前角色卡的名义发出这张图"),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": tr("fields.id", default="表情包 id")},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "id"],
        timeout=30.0,
    )
    async def send_entry(self, id: str = "", **kwargs):  # noqa: A002
        lanlan = _lanlan_from_kwargs(kwargs)
        sticker, failure = self._pick_for_send(id, lanlan)
        if failure is not None or sticker is None:
            return failure if failure is not None else Err(SdkError("sticker_not_found"))
        result = await self._sender.send(
            sticker, lanlan=lanlan, settings=self._settings, source="panel", now=time.time()
        )
        if not result.ok:
            return Err(SdkError(result.code))
        return Ok({"note": "sticker_sent", "id": sticker.id, "desc": sticker.desc})

    @ui.action(
        id="list",
        label=tr("actions.list.label", default="Search"),
        tone="info",
        refresh_context=False,
    )
    @plugin_entry(
        id="list",
        name=tr("entries.list.name", default="搜索表情包"),
        description=tr("entries.list.description", default="按关键词在库里搜（描述/标签/id 子串匹配），返回条目列表"),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": tr("fields.query", default="关键词，留空=全部")},
                "include_disabled": {
                    "type": "boolean",
                    "description": tr("fields.include_disabled", default="是否包含被禁用的"),
                },
            },
        },
        llm_result_fields=["note", "count"],
    )
    async def list_entry(self, query: str = "", include_disabled: bool = False, **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        pool = search_stickers(self._library.all(), query if isinstance(query, str) else "", include_disabled=True)
        rows = [s.as_dict() for s in pool if include_disabled or not s.disabled]
        return Ok({"note": "library_listed", "count": len(rows), "stickers": rows})

    @ui.action(
        id="preview",
        label=tr("actions.preview.label", default="Preview"),
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="preview",
        name=tr("entries.preview.name", default="取一张表情包的预览"),
        description=tr(
            "entries.preview.description",
            default="按段返回图片字节（base64）：面板逐段拉取拼回 dataUrl。分段是为了躲宿主控制通道单帧上限（实测 3.95MB 图整张回包会被拒发导致超时）",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": tr("fields.id", default="表情包 id")},
                "offset": {
                    "type": "integer",
                    "description": tr("fields.offset", default="从第几字节取（0 = 从头）"),
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        timeout=15.0,
    )
    async def preview_entry(self, id: str = "", offset: int = 0, **_):  # noqa: A002
        sticker = self._library.get(id) if isinstance(id, str) else None
        if sticker is None:
            return Err(SdkError("sticker_not_found"))
        try:
            data = self._library.image_path(sticker).read_bytes()
        except FileNotFoundError:
            return Err(SdkError("sticker_file_missing"))
        except Exception:
            return Err(SdkError("sticker_image_unreadable"))
        mime = _mime_for_file(sticker.file)
        if mime is None:
            return Err(SdkError("sticker_image_unreadable"))
        start = offset if isinstance(offset, int) and not isinstance(offset, bool) and offset > 0 else 0
        start = min(start, len(data))
        end = min(start + PREVIEW_CHUNK_BYTES, len(data))
        chunk = base64.b64encode(data[start:end]).decode("ascii")
        return Ok(
            {
                "note": "preview",
                "id": sticker.id,
                "mime": mime,
                "size": len(data),
                "offset": start,
                "next_offset": end,
                "chunk_base64": chunk,
                "done": end >= len(data),
            }
        )

    @ui.action(
        id="history",
        label=tr("actions.history.label", default="History"),
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="history",
        name=tr("entries.history.name", default="最近的表情包使用记录"),
        description=tr(
            "entries.history.description", default="返回发送台账尾部若干条（时刻/用了哪张/谁/成败），不含对话原文"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": tr("fields.limit", default="取几条")},
            },
        },
        llm_result_fields=["note", "count"],
    )
    async def history_entry(self, limit: int = 30, **_):
        take = limit if isinstance(limit, int) and not isinstance(limit, bool) and 0 < limit <= 200 else 30
        return Ok({"note": "usage_loaded", "count": take, "usage": self._library.read_usage(limit=take)})

    @ui.action(
        id="switch",
        label=tr("actions.switch.label", default="Toggle"),
        tone="warning",
        refresh_context=True,
    )
    @plugin_entry(
        id="switch",
        name=tr("entries.switch.name", default="开关表情包管理"),
        description=tr(
            "entries.switch.description",
            default="fail-closed 总开关：关闭后她不发图、看不到目录；库本身的增删改仍可用",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": tr("fields.enabled", default="是否开启")},
            },
            "required": ["enabled"],
        },
        llm_result_fields=["note", "enabled"],
    )
    async def switch_entry(self, enabled: bool = False, **_):
        if not isinstance(enabled, bool):
            return Err(SdkError("invalid_value"))
        try:
            await self.config.set("sticker_manager.enabled", bool(enabled))
        except Exception:
            self.logger.warning("failed to persist [sticker_manager].enabled", exc_info=True)
            return Err(SdkError("config_unavailable"))
        await self._reload_settings()
        return Ok({"note": "enabled" if enabled else "disabled", "enabled": self._settings.enabled})

    @ui.action(
        id="repair",
        label=tr("actions.repair.label", default="Repair"),
        tone="warning",
        refresh_context=True,
    )
    @plugin_entry(
        id="repair",
        name=tr("entries.repair.name", default="库体检与自修复"),
        description=tr(
            "entries.repair.description",
            default="清掉图片文件已丢失的条目、删掉没人引用的孤儿文件、补齐旧条目的内容指纹；返回各项计数",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["note", "removed_entries", "purged_files", "backfilled_hashes"],
        timeout=30.0,
    )
    async def repair_entry(self, **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        counts = self._library.repair()
        return Ok({"note": "library_repaired", **counts})

    @ui.action(
        id="import_inbox",
        label=tr("actions.import_inbox.label", default="Import"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="import_inbox",
        name=tr("entries.import_inbox.name", default="导入收件箱里的图片"),
        description=tr(
            "entries.import_inbox.description",
            default="把收件箱目录里的图片逐张收进库（描述取自文件名，重复自动跳过）；成功与重复的源文件会被删掉，超限/坏图保留原地可重试",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tags": {
                    "type": "string",
                    "description": tr("fields.tags", default="标签，逗号分隔（可选，整批共用）"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，整批共用；包里自带的优先）"),
                },
            },
        },
        llm_result_fields=["note", "imported", "duplicates", "rejected", "failed"],
        timeout=120.0,
    )
    async def import_inbox_entry(self, tags: str = "", group: str = "", **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        summary = self._library.ingest_inbox(tags=parse_tags_field(tags), group=normalize_group(group))
        return Ok({"note": "inbox_imported", **summary})

    # ------------------------------------------------------------------
    # 面板选择文件直传（v0.6.0）：zip 分块上传会话三步曲。
    # 为什么不走单 entry 整块 base64：控制面板的 ZeroMQ 帧上限呰不下套图包
    # （见 core.catalog UPLOAD_CHUNK_BYTES 注释）；为什么不只修面板：服务层
    # 需要逐块把关总量/乱序/会话寿命，这些纪律只能长在服务端。
    # 模型不需要这三个入口（它发图走 sticker_send，不搬运文件），但 entry 面
    # 就是面板/命令面板的 RPC 面，照旧双装饰。
    # ------------------------------------------------------------------

    @ui.action(
        id="import_upload_start",
        label=tr("actions.import_upload_start.label", default="Upload start"),
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="import_upload_start",
        name=tr("entries.import_upload_start.name", default="开始上传套图包"),
        description=tr(
            "entries.import_upload_start.description",
            default="面板选择文件直传第一步：开一个 .zip 上传会话，返回 session 与每块原始字节上限；面板专用通道，模型不需要调",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": tr("fields.upload_name", default="文件名（须以 .zip 结尾）")},
                "size": {
                    "type": "integer",
                    "description": tr("fields.upload_size", default="总字节数（仅用于提前拒绝明显超限）"),
                },
            },
            "required": ["name"],
        },
        llm_result_fields=["note", "session", "chunk_bytes"],
        timeout=30.0,
    )
    async def import_upload_start_entry(self, name: str = "", size: Any = None, **_):
        sid, error = self._library.upload_start(name, size=size)
        if error:
            return Err(SdkError(error))
        return Ok({"note": "upload_opened", "session": sid, "chunk_bytes": UPLOAD_CHUNK_BYTES})

    @ui.action(
        id="import_upload_chunk",
        label=tr("actions.import_upload_chunk.label", default="Upload chunk"),
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="import_upload_chunk",
        name=tr("entries.import_upload_chunk.name", default="上传一个分块"),
        description=tr(
            "entries.import_upload_chunk.description",
            default="把文件切块 base64 后按 seq 从 0 连续追加到上传会话；乱序/超限/会话已死会如实报错并作废会话",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": tr("fields.upload_session", default="上传会话 id")},
                "seq": {"type": "integer", "description": tr("fields.upload_seq", default="分块序号（从 0 连续）")},
                "data_base64": {
                    "type": "string",
                    "description": tr("fields.upload_chunk_base64", default="本块 base64（原始体≤chunk_bytes）"),
                },
            },
            "required": ["session", "seq", "data_base64"],
        },
        llm_result_fields=["note", "received"],
        timeout=30.0,
    )
    async def import_upload_chunk_entry(self, session: str = "", seq: int = -1, data_base64: str = "", **_):
        if not isinstance(session, str) or not session:
            return Err(SdkError("upload_session_unknown"))
        if not isinstance(seq, int) or isinstance(seq, bool):
            return Err(SdkError("upload_seq_gap"))
        if not isinstance(data_base64, str) or not data_base64:
            return Err(SdkError("upload_chunk_bad"))
        if len(data_base64) > _UPLOAD_B64_MAX_CHARS:
            return Err(SdkError("upload_too_large"))
        try:
            payload = base64.b64decode(data_base64, validate=False)
        except (binascii.Error, ValueError):
            return Err(SdkError("upload_chunk_bad"))
        if not payload:
            return Err(SdkError("upload_chunk_bad"))
        error = self._library.upload_append(session, seq, payload)
        if error:
            return Err(SdkError(error))
        return Ok({"note": "upload_chunked", "received": seq + 1})

    @ui.action(
        id="import_upload_finish",
        label=tr("actions.import_upload_finish.label", default="Upload finish"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="import_upload_finish",
        name=tr("entries.import_upload_finish.name", default="完成上传并导入套图包"),
        description=tr(
            "entries.import_upload_finish.description",
            default="关闭上传会话，把暂存的 zip 按收件箱认包同一把尺整批收进库（manifest v2 协议同款），返回四类计数；会话随即作废、暂存体删除",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": tr("fields.upload_session", default="上传会话 id")},
                "tags": {
                    "type": "string",
                    "description": tr("fields.tags", default="标签，逗号分隔（可选，整批共用）"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，整批共用；包里自带的优先）"),
                },
            },
            "required": ["session"],
        },
        llm_result_fields=["note", "imported", "duplicates", "rejected", "failed"],
        timeout=120.0,
    )
    async def import_upload_finish_entry(self, session: str = "", tags: str = "", group: str = "", **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        summary, error = self._library.upload_finish(session, tags=parse_tags_field(tags), group=normalize_group(group))
        if error:
            return Err(SdkError(error))
        return Ok({"note": "pack_uploaded", **summary})

    @ui.action(
        id="export_pack",
        label=tr("actions.export_pack.label", default="Export"),
        tone="info",
        refresh_context=False,
    )
    @plugin_entry(
        id="export_pack",
        name=tr("entries.export_pack.name", default="导出套图包"),
        description=tr(
            "entries.export_pack.description",
            default="把整本表情库打成一个套图 zip（图 + manifest）写到 exports 目录，返回文件路径；导入方放回收件箱即可整套收进",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["note", "exported", "skipped", "file"],
        timeout=120.0,
    )
    async def export_pack_entry(self, **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        result, error = self._library.export_pack(now=time.time())
        if error:
            return Err(SdkError(error))
        return Ok({"note": "pack_exported", **result})

    @ui.action(
        id="awareness_now",
        label=tr("actions.awareness_now.label", default="Ping"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="awareness_now",
        name=tr("entries.awareness_now.name", default="立刻注一条存在感"),
        description=tr(
            "entries.awareness_now.description",
            default="调试用：绕过间隔闸，立刻给当前角色卡注一条'你有表情包'的静默提示（用户看不见）",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["note", "status"],
        timeout=15.0,
    )
    async def awareness_now_entry(self, **kwargs):
        lanlan = _lanlan_from_kwargs(kwargs)
        result = await self._awareness.inject_now(settings=self._settings, lanlan=lanlan, now=time.time())
        status = str(result.get("status", ""))
        if status in {"disabled", "no_target", "empty_library"}:
            return Err(SdkError(f"awareness_{status}"))
        return Ok({"note": "awareness", "status": status})

    # ------------------------------------------------------------------
    # 面板上下文
    # ------------------------------------------------------------------

    @ui.context(id="dashboard", title=tr("panel.title", default="表情包管理"))
    async def dashboard_context(self, **kwargs: Any) -> dict[str, Any]:
        loaded = self._library.load()
        settings = self._settings
        lanlan = _lanlan_from_kwargs(kwargs)
        stickers = self._library.all()
        group_descs = self._library.group_descs()
        group_counts: dict[str, int] = {}
        for s in stickers:
            if s.group:
                group_counts[s.group] = group_counts.get(s.group, 0) + 1
        groups = [
            {"name": n, "count": group_counts.get(n, 0), "desc": group_descs.get(n, "")}
            for n in sorted(set(group_counts) | set(group_descs))
        ]
        payload: dict[str, Any] = {
            "enabled": settings.enabled,
            "lanlan": lanlan,
            "counts": {
                "total": len(stickers),
                "enabled": sum(1 for s in stickers if not s.disabled),
                "sent_total": sum(s.use_count for s in stickers),
                "groups": len(groups),
            },
            "stickers": [s.as_dict() for s in stickers],
            "groups": groups,
            "group_descs": group_descs,
            "usage": self._library.read_usage(limit=12),
            "inbox": {
                "pending": len(self._library.inbox_files()),
                "path": str(self._library.inbox_dir),
            },
            "awareness": self._awareness.snapshot(interval_sec=settings.awareness.interval_sec, now=time.time()),
            "config": {
                "cooldown_sec": settings.send.cooldown_sec,
                "inline_max_bytes": settings.send.inline_max_bytes,
                "catalog_limit_for_model": settings.storage.catalog_limit_for_model,
                "recent_dedup_count": settings.send.recent_dedup_count,
                "probability": settings.send.probability,
                "probability_reuse_sec": settings.send.probability_reuse_sec,
                "awareness_enabled": settings.awareness.enabled,
                "awareness_interval_sec": settings.awareness.interval_sec,
            },
        }
        if not loaded.ok:
            payload["error_code"] = loaded.code
        return payload

    # ------------------------------------------------------------------
    # LLM 工具：她可以自主调用
    # ------------------------------------------------------------------

    @llm_tool(
        name="sticker_list",
        description=(
            "看看你收藏的表情包里有什么。不填参数：先看套图分类（名字+张数+什么时候用），"
            "再看你的常货；填 group 只看那一组的完整清单；填 query 按描述/梗义/标签/组名搜。"
            "拿到后用 sticker_send 发（也可以 sticker_send 只给 group，组内帮你选）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "想找什么样的表情（如：开心/无语/猫猫），可不填"},
                "group": {"type": "string", "description": "只看这个套图分组的全部图（组名从分类行里拿）"},
            },
            "required": [],
        },
        timeout=10.0,
    )
    async def tool_sticker_list(self, query: str = "", group: str = "", **kwargs: Any) -> dict[str, Any]:
        if not self._settings.enabled:
            return {"ok": False, "reason": "not_enabled"}
        self._library.load()
        limit = self._settings.storage.catalog_limit_for_model
        all_stickers = self._library.all()
        descs = self._library.group_descs()
        gname = normalize_group(group)
        if gname:
            names = sorted({s.group for s in all_stickers if s.group})
            matched = _match_group_names(names, gname)
            if len(matched) > 1:
                overview = format_group_overview(
                    [s for s in all_stickers if s.group in matched], {n: descs.get(n, "") for n in matched}
                )
                return {
                    "ok": True,
                    "count": len(matched),
                    "catalog": overview,
                    "note": "group 撞了多个组名——更准的名字再来一次",
                }
            if not matched:
                return {
                    "ok": True,
                    "count": 0,
                    "catalog": "",
                    "note": "没有这个分组；不填 group 可以看到分类列表",
                }
            pool = search_stickers([s for s in all_stickers if s.group == matched[0]], query, include_disabled=False)
            catalog = format_catalog_for_model(pool, limit, descs)
            header = f"「{matched[0]}」" + (f"：{descs[matched[0]]}" if descs.get(matched[0]) else "")
            return {
                "ok": True,
                "count": pool and len(pool) or 0,
                "catalog": f"{header}\n{catalog}" if catalog else header,
            }
        pool = search_stickers(all_stickers, query if isinstance(query, str) else "", include_disabled=False)
        catalog = format_catalog_for_model(pool, limit, descs)
        if not catalog:
            return {"ok": True, "count": 0, "catalog": "", "note": "库里还没有表情包，或没有匹配的"}
        if not (isinstance(query, str) and query.strip()):
            # 轮 F 两级目录：无搜索词时先给分类行再给条目——她先看“有哪些套图”，
            # 下钻用 group 参数（对齐外部系统“分类即 prompt”的心智，只是我们在工具结果里给）。
            overview = format_group_overview(all_stickers, descs)
            if overview:
                catalog = f"【套图分类】\n{overview}\n【条目】\n{catalog}"
        return {"ok": True, "count": catalog.count("\n") + 1, "catalog": catalog}

    @llm_tool(
        name="sticker_send",
        description=(
            "发一张表情包到聊天里。给 id 最准；给 group：从那个套图分组里帮你选一张（组内随机，"
            "刚发过的会自动排除）；给关键词：筛得只剩一张就直接发，候选不止一张会把清单回给你——"
            "看一眼再用 id 发第二刀。什么都没给会拒。发不出去会告诉你原因，别连试；"
            "刚发过的会被'最近不重复'挡下，只有主人点名要再看某张时才带 force=true 绕行。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "sticker_id": {"type": "string", "description": "表情包的 id（首选）"},
                "query": {"type": "string", "description": "没有 id 时给关键词（想表达的态度/场景，会按梗义筛）"},
                "group": {
                    "type": "string",
                    "description": "只给套图分组名：组内随机选一张（适合“这组调性对，具体哪张你定”）",
                },
                "force": {
                    "type": "boolean",
                    "description": "仅当主人明确点名要再看/再发这张时置 true：跳过最近不重复与概率闸（冷却仍生效）",
                },
            },
            "required": [],
        },
        timeout=20.0,
    )
    async def tool_sticker_send(
        self, sticker_id: str = "", query: str = "", group: str = "", force: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        if not self._settings.enabled:
            return {"ok": False, "reason": "not_enabled"}
        lanlan = _lanlan_from_kwargs(kwargs)
        self._library.load()
        settings = self._settings
        bypass = bool(force)
        # 去重（轮 D①）：query 候选池剔除"她最近发过的 N 张"（同一角色卡）。
        # 显式 id 不在这拦——那是 sender.send 里和冷却/概率闸一起过的同一把尺，
        # 保证 tool 以外没有第二条能绕开的通道。
        recent = (
            self._sender.recent_sent_ids(lanlan, settings)
            if (not bypass and settings.send.recent_dedup_count > 0)
            else set()
        )
        sticker = None
        has_id = isinstance(sticker_id, str) and bool(sticker_id.strip())
        has_query = isinstance(query, str) and bool(query.strip())
        has_group = isinstance(group, str) and bool(normalize_group(group))
        pool = self._library.all()
        if has_id:
            sticker = self._library.get(sticker_id.strip())
            if sticker is not None and sticker.disabled:
                sticker = None
        candidates: list[Sticker] = []
        if sticker is None and has_group and not has_query:
            # 轮 F 组内选图（对齐外部系统“模型只挑分类，图由系统定”）：候选池先过同一把
            # 近期去重尺，再组内随机——她不需要逐图 id，节奏层照常把关。
            gname = normalize_group(group)
            names = sorted({s.group for s in pool if s.group})
            matched = _match_group_names(names, gname)
            if len(matched) > 1:
                return {
                    "ok": True,
                    "sent": "",
                    "note": "group_candidates",
                    "count": len(matched),
                    "candidates": "\n".join(f"・{n}" for n in matched),
                    "hint": "组名撞了多个——用更准的组名或组内某张的 id 再发一次。",
                }
            if not matched:
                known = "\n".join(f"・{n}" for n in names)
                return {
                    "ok": False,
                    "reason": "group_not_found",
                    "hint": f"没有这个分组。现有分组：\n{known}" if known else "库里还没有任何分组。",
                }
            group_pool = [s for s in pool if s.group == matched[0] and not s.disabled]
            if not group_pool:
                return {"ok": False, "reason": "group_empty", "hint": "这一组没有可用图——换个组或文字回。"}
            selectable = [s for s in group_pool if s.id not in recent] if recent else group_pool
            if not selectable:
                return {
                    "ok": False,
                    "reason": "recent_repeat",
                    "hint": "这一组最近的全发过了——换组、用文字回；主人点名才 force。",
                }
            sticker = _GROUP_PICK(selectable)
        if sticker is None and has_query:
            filtered = [s for s in pool if s.id not in recent] if recent else pool
            sticker, candidates = resolve_send_target(filtered, query)
            if sticker is None and not candidates and recent:
                # 区分"根本没匹配"和"匹配的全是刚发过的"——前者让她换词，
                # 后者让她换一张而不是重试同一刀。
                old_sticker, old_candidates = resolve_send_target(pool, query)
                if old_sticker is not None or old_candidates:
                    return {
                        "ok": False,
                        "reason": "recent_repeat",
                        "hint": "匹配的全是最近发过的——换一张或干脆用文字回；"
                        "只有主人点名要再看这张才用 force=true 重发。",
                    }
        if candidates:
            # 头部并列：不替她拍板。回候选清单（行形状与 sticker_list 同一把尺），
            # 不算失败——"看到了、还没选"是选图流程的中间态。
            catalog = format_catalog_for_model(candidates, len(candidates), self._library.group_descs())
            return {
                "ok": True,
                "sent": "",
                "note": "multi_candidates",
                "count": len(candidates),
                "candidates": catalog,
                "hint": "分不清哪张最贴——用上面的 id 再发一次 sticker_send",
            }
        if sticker is None:
            # id 没点到东西→如实说没有；三者都没给→拒空枪（旧行为会"顺手"发常货，
            # 轮 C 起选图必须有依据；轮 F 起 group 也是依据——那是它"发得准"的另一半）。
            return {
                "ok": False,
                "reason": "no_match" if (has_id or has_query or has_group) else "id_or_query_required",
            }
        result = await self._sender.send(
            sticker,
            lanlan=lanlan,
            settings=settings,
            source="tool",
            now=time.time(),
            force=bypass,
        )
        if not result.ok:
            self._sender.note_attempt_failed(
                lanlan=lanlan,
                sticker_id=sticker.id,
                code=result.code,
                settings=self._settings,
                now=time.time(),
            )
            out: dict[str, Any] = {"ok": False, "reason": result.code, "tried": sticker.id}
            hint = _SEND_HINTS.get(result.code)
            if hint:
                out["hint"] = hint
            return out
        return {"ok": True, "sent": sticker.id, "desc": sticker.catalog_body(self._library.group_descs())}

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _reload_settings(self) -> StickerManagerSettings:
        try:
            config = await self.config.dump()
        except Exception:
            self.logger.warning("config dump failed; keeping previous settings", exc_info=True)
            return self._settings
        self._settings = StickerManagerSettings.from_config(config if isinstance(config, dict) else {})
        return self._settings

    def _pick_for_send(self, sticker_id: Any, lanlan: str) -> tuple[Sticker | None, Any | None]:
        loaded = self._library.load()
        if not loaded.ok:
            return None, Err(SdkError(loaded.code))
        if not isinstance(sticker_id, str) or not sticker_id.strip():
            return None, Err(SdkError("sticker_not_found"))
        sticker = self._library.get(sticker_id.strip())
        if sticker is None:
            return None, Err(SdkError("sticker_not_found"))
        _ = lanlan
        return sticker, None


def _lanlan_from_kwargs(kwargs: dict[str, Any]) -> str:
    """从入口/工具收到的 `_ctx` 里取角色名（宿主每次调用都会注入）。"""
    ctx_obj = kwargs.get("_ctx") if isinstance(kwargs, dict) else None
    if isinstance(ctx_obj, dict):
        name = ctx_obj.get("lanlan_name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return ""


def _mime_for_file(file_name: str) -> str | None:
    lowered = file_name.lower()
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".gif"):
        return "image/gif"
    if lowered.endswith(".webp"):
        return "image/webp"
    return None
