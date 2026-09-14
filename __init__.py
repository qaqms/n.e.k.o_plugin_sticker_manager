"""表情包管理器 (sticker_manager) —— 让她收藏、送出自己的表情包。

架构（一段话）：`core/` 是零 SDK 依赖的纯函数层（条目形状 / 格式嗅探 / 检索 /
目录文案 / 配置视图），`services/` 是有状态层（`data/` 下的 JSON 目录 + 图片文件、
发送链路与冷却台账），本模块只做装配与对外契约面（entry / ui.context / ui.action /
llm_tool）。

v0.1.0「能收能发」：主人在面板里收藏表情包（上传、写描述、打标签、禁用、删除），
她通过 `sticker_list` / `sticker_send` 两个工具在对话里自主挑一张发出去；
发送走 `push_message` 的 image part（≤ 内联预算直发原字节，大图换 URL part），
每次成败都记进使用台账，面板能看到"她最近爱用什么"。

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
import time
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    tr,
    ui,
)

from .core import (
    Sticker,
    StickerManagerSettings,
    format_catalog_for_model,
    parse_tags_field,
    search_stickers,
    validate_desc,
)
from .services import Library, Sender

__all__ = ["StickerManagerPlugin"]

# 面板预览的读取上限：超过就不给 dataUrl（表情包不该是这个量级）。
_PREVIEW_MAX_BYTES = 4 * 1024 * 1024
# 上传解码前的字节上限提示（真正的 8 MiB 判定在 add 入口）。
_MAX_BASE64_CHARS = 12 * 1024 * 1024


@neko_plugin
class StickerManagerPlugin(NekoPluginBase):
    """表情包收藏与发送：面板管理库，她在对话里自主发图。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._settings = StickerManagerSettings.defaults()
        self._library = Library(self.data_path("library"), logger=self.logger)
        self._sender = Sender(self, self._library, logger=self.logger)

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
            default="把一张图片存进表情库：data_base64 是图片本体（不含 data: 前缀），desc 是她选图的唯一依据",
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
                    "description": tr("fields.desc", default="一句话描述图里在干什么（≤200字）"),
                },
                "tags": {
                    "type": "string",
                    "description": tr("fields.tags", default="标签，逗号分隔（可选）"),
                },
            },
            "required": ["data_base64", "desc"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "id", "desc"],
        timeout=30.0,
    )
    async def add_entry(self, data_base64: str = "", desc: str = "", tags: str = "", **_):
        text_desc, desc_error = validate_desc(desc)
        if desc_error == "empty":
            return Err(SdkError("desc_required"))
        if desc_error == "too_long":
            return Err(SdkError("desc_too_long"))
        if not isinstance(data_base64, str) or not data_base64:
            return Err(SdkError("image_required"))
        if len(data_base64) > _MAX_BASE64_CHARS:
            return Err(SdkError("image_too_large"))
        try:
            payload = base64.b64decode(data_base64, validate=False)
        except (binascii.Error, ValueError):
            return Err(SdkError("image_undecodable"))
        if len(payload) > 8 * 1024 * 1024:
            return Err(SdkError("image_too_large"))
        sticker, error = self._library.add(
            data=payload,
            desc=text_desc,
            tags=parse_tags_field(tags),
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
            default="改描述/标签/禁用状态。描述决定她会不会选中这张图，禁用=从她的可选面里摘掉",
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
        **_,
    ):
        if not isinstance(id, str) or not id:
            return Err(SdkError("sticker_not_found"))
        new_desc: str | None = None
        if isinstance(desc, str) and desc.strip():
            text_desc, desc_error = validate_desc(desc)
            if desc_error:
                return Err(SdkError(f"desc_{desc_error}"))
            new_desc = text_desc
        new_tags: list[str] | None = None
        if tags is not None and not (isinstance(tags, str) and not tags.strip()):
            new_tags = parse_tags_field(tags)
        if disabled is not None and not isinstance(disabled, bool):
            return Err(SdkError("invalid_value"))
        sticker, error = self._library.update(id, desc=new_desc, tags=new_tags, disabled=disabled)
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
        if failure is not None:
            return failure
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
        pool = search_stickers(
            self._library.all(), query if isinstance(query, str) else "", include_disabled=True
        )
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
        description=tr("entries.preview.description", default="返回 dataUrl（base64 图片），面板懒加载缩略图用"),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": tr("fields.id", default="表情包 id")},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        timeout=15.0,
    )
    async def preview_entry(self, id: str = "", **_):  # noqa: A002
        sticker = self._library.get(id) if isinstance(id, str) else None
        if sticker is None:
            return Err(SdkError("sticker_not_found"))
        try:
            data = self._library.image_path(sticker).read_bytes()
        except FileNotFoundError:
            return Err(SdkError("sticker_file_missing"))
        except Exception:
            return Err(SdkError("sticker_image_unreadable"))
        if len(data) > _PREVIEW_MAX_BYTES:
            return Err(SdkError("sticker_too_large"))
        mime = _mime_for_file(sticker.file)
        if mime is None:
            return Err(SdkError("sticker_image_unreadable"))
        encoded = base64.b64encode(data).decode("ascii")
        return Ok({"note": "preview", "id": sticker.id, "data_url": f"data:{mime};base64,{encoded}"})

    @ui.action(
        id="history",
        label=tr("actions.history.label", default="History"),
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="history",
        name=tr("entries.history.name", default="最近的表情包使用记录"),
        description=tr("entries.history.description", default="返回发送台账尾部若干条（时刻/用了哪张/谁/成败），不含对话原文"),
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

    # ------------------------------------------------------------------
    # 面板上下文
    # ------------------------------------------------------------------

    @ui.context(id="dashboard", title=tr("panel.title", default="表情包管理器"))
    async def dashboard_context(self, **kwargs: Any) -> dict[str, Any]:
        loaded = self._library.load()
        settings = self._settings
        lanlan = _lanlan_from_kwargs(kwargs)
        stickers = self._library.all()
        payload: dict[str, Any] = {
            "enabled": settings.enabled,
            "lanlan": lanlan,
            "counts": {
                "total": len(stickers),
                "enabled": sum(1 for s in stickers if not s.disabled),
                "sent_total": sum(s.use_count for s in stickers),
            },
            "stickers": [s.as_dict() for s in stickers],
            "usage": self._library.read_usage(limit=12),
            "config": {
                "cooldown_sec": settings.send.cooldown_sec,
                "inline_max_bytes": settings.send.inline_max_bytes,
                "catalog_limit_for_model": settings.storage.catalog_limit_for_model,
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
            "看看你收藏的表情包里有什么。不填 query 就是全部（按你最近爱用的排），"
            "填了就按关键词搜。拿到列表后用 sticker_send 发。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "想找什么样的表情（如：开心/无语/猫猫），可不填"},
            },
            "required": [],
        },
        timeout=10.0,
    )
    async def tool_sticker_list(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        if not self._settings.enabled:
            return {"ok": False, "reason": "not_enabled"}
        self._library.load()
        pool = search_stickers(
            self._library.all(), query if isinstance(query, str) else "", include_disabled=False
        )
        catalog = format_catalog_for_model(pool, self._settings.storage.catalog_limit_for_model)
        if not catalog:
            return {"ok": True, "count": 0, "catalog": "", "note": "库里还没有表情包，或没有匹配的"}
        return {"ok": True, "count": catalog.count("\n") + 1, "catalog": catalog}

    @llm_tool(
        name="sticker_send",
        description=(
            "发一张表情包到聊天里。可以先用 sticker_list 看看有什么；直接给 id 最准，"
            "给关键词就发匹配到的第一张。发不出去会告诉你原因，别连试。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "sticker_id": {"type": "string", "description": "表情包的 id（首选）"},
                "query": {"type": "string", "description": "没有 id 时给关键词"},
            },
            "required": [],
        },
        timeout=20.0,
    )
    async def tool_sticker_send(self, sticker_id: str = "", query: str = "", **kwargs: Any) -> dict[str, Any]:
        if not self._settings.enabled:
            return {"ok": False, "reason": "not_enabled"}
        lanlan = _lanlan_from_kwargs(kwargs)
        self._library.load()
        sticker = None
        if isinstance(sticker_id, str) and sticker_id.strip():
            sticker = self._library.get(sticker_id.strip())
            if sticker is not None and sticker.disabled:
                sticker = None
        if sticker is None:
            pool = search_stickers(
                self._library.all(), query if isinstance(query, str) else "", include_disabled=False
            )
            sticker = pool[0] if pool else None
        if sticker is None:
            return {"ok": False, "reason": "no_match"}
        result = await self._sender.send(
            sticker, lanlan=lanlan, settings=self._settings, source="tool", now=time.time()
        )
        if not result.ok:
            self._sender.note_attempt_failed(
                lanlan=lanlan, sticker_id=sticker.id, code=result.code,
                settings=self._settings, now=time.time(),
            )
            return {"ok": False, "reason": result.code, "tried": sticker.id}
        return {"ok": True, "sent": sticker.id, "desc": sticker.desc}

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
