# 表情包管理 (sticker_manager) Design Brief

## Identity Lock
- plugin_id: `sticker_manager`
- folder: 开发仓 `n.e.k.o_plugin_sticker_manager`；挂载态必须叫 `sticker_manager`（目录名==entry 包名，软链接无效，唯一挂载方式是复制）
- name: 表情包管理
  （注：插件中心**已安装卡片**的显示名优先读插件 i18n 的 `plugin.name` 键，回落才是本字段——
  改显示名必须两处一起改；链路：宿主 `frontend/plugin-manager/src/utils/pluginDisplay.ts`，
  在线市场卡 `MarketPluginCard` 则直接用本字段）
- entry: `plugin.plugins.sticker_manager:StickerManagerPlugin`
- main class: `StickerManagerPlugin`

## Purpose
给宿主主对话里的猫娘一个**自己的表情包收藏间**：主人在面板里收藏/描述/打标签/禁用/删除，
她通过 llm_tool 在对话里自主挑一张发出去；每次成败记使用台账。

## Package Type and Capabilities
- package type: plugin（独立功能，不挂宿主扩展点）
- capabilities: UI 面板（hosted-tsx）+ entries + llm_tools + 生命周期 + 文件持久化
- inferred architecture: core 纯函数层 / services 有状态层 / 根模块只做装配（our_life 同款三段式）

## First Version Scope
- 表情库：`data/library/catalog.json` + `data/library/stickers/<id>.<ext>` + `data/library/usage.json`
- 格式：png / jpg / gif / webp，**只认文件头魔数**；单张 ≤8MiB
- 入口面：add / update / remove / send / list / preview / history / switch / repair / import_inbox（全 `@ui.action`）+ `@ui.context("dashboard")`
- 批量导入（v0.1.2，思路参致 astrbot）：面板原生 multiple 文件框逐张走 add 通道；`data/library/inbox/` 目录由 `import_inbox` 服务端整批收（描述取自文件名，成功/重复源删、超限/坏图留）
- 内容指纹查重（v0.1.1）：入库记 sha256，同图回 `duplicate_image`；旧条目在查重/体检时 lazy 回填（catalog schema 不变，宽松兼容）
- 工具面：`sticker_list`（目录）、`sticker_send`（按 id 或关键词发）
- 工具注册心跳（v0.1.4，移植 our_life v0.5.0）：`@timer_interval("watch", 60s)` 拍上挂
  `services/tool_watch.ToolWatch`（300s 自节流，首拍即查）：回环 `GET /api/tools` 点名缺席、
  只对真缺席补挂（IPC 重发 replace 幂等）；不可达零动作；永不炸拍
- 发送链路：≤256KiB 内联 image data part（gif 恒走内联保动画）；更大走 `ctx.images.upload()` 换 URL part
- 频控：按角色卡内存冷却（默认 20s）；`push_message(visibility=["chat"], ai_behavior="read")`
- i18n：zh-CN + en（Python `tr()` 与 TSX `t()` 键全部入文件，有门钉着）

## Out of Scope（v0.1.0 刻意不做）
- 宿主 proactive_chat 的**在线 meme 图源链路**（meme_fetcher 抓图）——平台层，插件无 hook，管不到也不该管
- qq_auto_reply 的 QQ 表情目录联动（`call_entry` 通道存在，要做需单独立项）
- 表情包分组/套图（tags 已留扩展位）
- 定期目录注入（现在靠她主动调 `sticker_list`；若实测"她想不起来有表情"再加注入通道）
- ~~工具重注册心跳~~ —— **v0.1.4 已做**（our_life v0.5.0 方案移植，见上面的工具面与陷阱 15）

## Inferred Technical Needs
- plugin.toml：`[plugin]` `[plugin.sdk]` `[plugin.i18n]` `[plugin.ui]+panel` `[plugin_runtime]`(auto_start=true) + 业务段 `[sticker_manager]/.send/.storage`
- 不声明 `[plugin.store]`：持久化走 `data_path` 文件通道（失败是响亮的，规避 store 静默失效坑）
- SDK surfaces：`plugin.sdk.plugin` 唯一门面；`ctx.push_message` / `ctx.images.upload`（仅 entry/tool 里用，lifecycle 不可）
- UI：hosted-tsx；`ImageUpload`/`ImagePreview` 是 kit 现成件；缩略图懒加载走 `preview` action（context 不带图字节）
- 错误码契约：`^[a-z][a-z0-9_]*$` 稳定 ASCII（invalid_image / duplicate_image / sticker_not_found / send_cooldown / not_enabled / sticker_disabled / sticker_too_large / sticker_file_missing / library_io_error / config_unavailable / desc_required / desc_too_long / image_too_large / image_undecodable）

## 已知陷阱（本机/宿主源码核实，改动前先读）
1. `ctx.images.upload()` 会把图**归一成 JPEG**——动图被压平，所以 gif 只走内联，内联不下就如实拒绝（`services/sender.py`）。
2. 角色归属只认本次调用注入的 `_ctx["lanlan_name"]`；`ctx._current_lanlan` 是脏值。
3. `push_message` 的 `submitted=True` ≠ 宿主已消费；lifecycle 里推送会被静默丢弃（本插件只在 entry/tool 里发）。
4. 整条 payload ≤512KiB：`inline_max_bytes` 默认 256KiB 留了 base64 膨胀（4/3）与封装余量。
5. 挂载态目录名必须等于 entry 包名；仓名 `n.e.k.o_plugin_sticker_manager` 永远挂不上，只能复制（release_gate 已代劳）。
6. 三处同源：`core/configuration.py` 默认值 == `plugin.toml` 业务段 == `config.example.toml`（`tests/test_config_docs_sync.py` 钉死）；改配置三处一起改。
7. i18n 键插入必须文本级（json.dump 会重排+CRLF→LF）；TSX 检查器是文本级规则：**裸 `api` 标识符直接拒收**，一律 `props.surface.api` 成员访问。
8. release 门要求挂载副本里有 `tests/test_smoke.py`（manifest 扫描看源码树），且副本不能含高压缩比垃圾目录（.tmpgate 已排除）。
9. 冷却在内存：重启清零是刻意行为，别"顺手"持久化。
10. ruff 门跑 `--ignore-noqa`：noqa 注释不作数，E731（lambda 赋值）这类要真的改掉。
11. **文件名→描述清洗有两份**（`core.catalog.desc_from_filename` 与面板 `guessDesc`）：
    iframe 碰不到 Python，这是跨运行时的必要重复不是偷懒——改规则必须两边一起改，
    否则批量两通道入库的描述形态会分叉。同理单张上限 `MAX_STICKER_BYTES`（Python）与
    面板同名常量两处同数。
12. **hosted TSX 里 `Array.from(x || [])` 推成 `unknown[]`**：取 `.size/.name` 直接
    tsc 报错（hosted-tsx 门真跑类型检查）——要写 `const list: any[] = Array.from(...)`。
13. 收件箱导入的处置纪律：**成功/重复的源文件删，超限/坏图留**（删留着重试）；
    删重复件是因为不删会每轮重报同一批；隐藏项（点开头）不碰不删。
14. **entry 回包走宿主 ZeroMQ 控制通道，单帧硬上限 4,784,128 字节**
    （`plugin/settings.py` PLUGIN_ZMQ_CONTROL_UPLINK_MAX_BYTES，Steam 实机日志钉的坑：
    3.95MB 图整张 dataUrl=5.27MB 被传输层拒发，宿主干等 15s 超时→面板 500）。
    所以 preview 是**分段协议**（offset → chunk_base64/next_offset/done，宽 3MiB=3 的倍数
    保证 base64 无填充可串接）；任何入口都不许把 MB 级 base64 塞进返回值。
    注意发送链路的 `images.upload` 走的是另一条专用媒体通道（单张 8MiB），不受此限。
15. **心跳补挂只能走基类 protected 面**（`_notify_llm_tool_registered`，一律 `getattr`）：
    公开 `register_llm_tool` 重调会撞 SDK `_llm_tools` 里的同名条目（EntryConflictError），
    "先 unregister 再 register"有本地已删、远端又失败的窗口——都比原地重发更糟。
    另外两条纪律：不可达≠缺席（main_server 没起时盲重注册是每拍追打）；
    `no_tools`（工具还没收集齐）**不推进时钟**，收集齐后下一拍就查。心跳与 `[].enabled`
    无关：注册韧性是在场性，不随业务冻结而冻结。

## Read Context Plan
- `N.E.K.O/.agent/skills/neko-plugin/**`（契约）→ `plugin/sdk/plugin/base.py`、`plugin/core/context.py`（images/push 语义）
- 同工作区 `n.e.k.o_plugin_our_life`（工程基线与五门）；`plugin/plugins/qq_auto_reply`（sticker 目录注入先例）
- `问题清单/已知问题.md`（本机环境坑）

## Write Workspace
`F:\ai\neko kaifa2\n.e.k.o_plugin_sticker_manager`（独立 Git 仓；宿主仓同级）

## Risk Follow-ups
- ~~llm_tool 注册表在宿主重启后即丢且无自动重注册~~ —— **v0.1.4 已解**
  （`services/tool_watch.py`，our_life v0.5.0 同方案；间隔 300s，与 fc/our_life 同量级）。
- 上传图无内容审核：库是主人手动收藏的，风险面与在线图源不同；若未来开"她自己去网上抓图入库"，必须接宿主 `utils/meme_moderation` 等价物。
