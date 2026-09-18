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
- 批量导入（v0.1.2，思路参致外部系统）：面板原生 multiple 文件框逐张走 add 通道（**v0.10.0 轮 I 起收图不再拿文件名当描述**，描述交空串走回落尺）；`data/library/inbox/` 目录由 `import_inbox` 服务端整批收（描述取自文件名，成功/重复源删、超限/坏图留）
- 内容指纹查重（v0.1.1）：入库记 sha256，同图回 `duplicate_image`；旧条目在查重/体检时 lazy 回填（catalog schema 不变，宽松兼容）
- 工具面：`sticker_list`（目录）、`sticker_send`（id 或关键词）；轮 C（v0.4.0）升级：
  query 经 `resolve_send_target` 判定——最优严格唯一直发，头部并列回 top-5 候选清单
  （`multi_candidates`）让她拿 id 二次定夺；空枪（id/query 都不给）拒 `id_or_query_required`；
  检索唯一实现 `search_with_scores`，三处同源
- 工具注册心跳（v0.1.4，移植 our_life v0.5.0）：`@timer_interval("watch", 60s)` 拍上挂
  `services/tool_watch.ToolWatch`（300s 自节流，首拍即查）：回环 `GET /api/tools` 点名缺席、
  只对真缺席补挂（IPC 重发 replace 幂等）；不可达零动作；永不炸拍
- 存在感注入（v0.2.0）：同一个 60s 拍再挂 `services/awareness.Awareness`——从 `bus.conversations`
  找最近在跟她说话的角色卡，低频（默认 3600s/卡，`[sticker_manager.awareness]`）静默注入
  「收藏间里有 N 张 + 最近常用前 K 行」（`visibility=[]` + `ai_behavior="read"`）；
  空库/无目标/被拒不推进时钟；永不炸拍；调试入口 `awareness_now`（绕节奏不绕开关）
- 套图分组（v0.2.0）：`Sticker.group`（至多一个、可空=未分组；老库宽松兼容）；检索打分、
  目录行 `[id] 描述（套图：G；标签：a/b）`、面板 chips 过滤与编辑框全贯通
- 套图包导出/导入（v0.2.0）：`data/library/exports/*.zip`（manifest.json + stickers/）；
  导入走收件箱通道认 `.zip`（manifest 优先、裸图包按文件名清洗）；zip 条目名只当包内定位符，
  落盘永远走 add() 服务端发号（zip-slip 免疫）；刻意不兼容外部系统的 memes_data.json
- 选择文件直传（v0.6.0 轮 E）：面板「导入」（轮 E 时叫「选择套图包导入」，v0.10.3 改短）→ `.zip` 分块上传会话
  （`import_upload_start/chunk/finish` 三入口，块大小服务端定）——收件箱不再是唯一包入口，
  v0.10.3 起进一步降为**纯服务端旁路**：面板的「导入收件箱」按钮与路径提示行全部退场（主人拍板：
  导入出口只留一个），`import_inbox` 入口与处置纪律（陷阱 13）原样保留。纪律：会话只存本进程内存 + `data/uploads/.sid.part`（重启即作废，
  不做断点续传这种短命交互的复杂度）；seq 乱序/超限/写失败一律**作废会话**不静默拼接；
  finish 走 import_pack 同一把尺，暂存体无论成败都删（会话一次性，重试=重选文件）；
  新错误码 upload_not_zip / upload_session_unknown / upload_seq_gap / upload_chunk_bad /
  upload_too_large / upload_empty / upload_write_failed；上传方向同受 ZMQ 帧上限（陷阱 14 的反方向）
- 语义元数据层（v0.3.0 轮 A，学习外部系统 数据层，见 docs/sticker-system-study.md）：
  `Sticker.caption`（梗义 ≤300，"这张图在回复什么/什么上一句触发"，可选）+
  `Sticker.visible_text`（图内原文 ≤200，只检索不上目录）；目录行正文 `catalog_body()`
  一把尺（caption 优先回落 desc，sticker_list 与 awareness 同源自动同步）；
  打分序 desc>caption>套图精确>标签>套图子串>图内原文>文件名；update 的 caption 空串=清除；
  新错误码 caption_too_long / visible_text_too_long；拷贝重建改 dataclasses.replace
  （灭"手工枚举漏字段"整类雷，sha256 回归的真病根）；manifest v2 随包携带，旧库/旧包宽松兼容
- 发送链路：≤256KiB 内联 image data part（gif 恒走内联保动画）；更大走 `ctx.images.upload()` 换 URL part
- 频控与节奏（轮 D 后现状）：三层分离——**冷却**按角色卡内存表（默认 20s）；
  **跨轮去重**读持久台账（`send.recent_dedup_count`，默认 5，同角色卡最近 N 张成功
  发出的图在自主选图时不可选：query 池剔除/显式 id 发送时拦，`force` 绕行只给主人
  点名场景）；**概率闸门**`send.probability`（默认 1.0=关）同角色卡按
  `probability_reuse_sec` 窗口复用判定不重掷（p² 教训，外部系统只掷一次存 extra 的
  插件侧等价物）。软提示进 awareness 文案与工具描述，硬闸在 sender——两层分离。
  投递：`push_message(visibility=["chat"], ai_behavior="read")`
- 轻打标（v0.7.0 轮 F，对齐外部系统“分类=描述”心智）：① 逐图 desc 可选，目录行正文回落
  `caption>desc>分组说明>套图兜底>未标注`（一把尺，`catalog_body(groups)`）；② 分组说明挂
  `catalog.json.groups = {组名: 一句话}`（≤300，只能给有图在用的组写），`group_set_desc` 入口 +
  面板 chips 下编辑框；③ 两级目录：`sticker_list` 无词先回【套图分类】再回【条目】、`group=` 下钻；
  ④ `sticker_send(group=)` 组内选图：id>group>query，组名精确>子串多命中回 `group_candidates`，
  候选池先过近期去重尺再 `_GROUP_PICK` 随机（模块级可注入，同 _RNG 纪律）；剔到空回 `recent_repeat`；
  ⑤ 批量整理：`batch_update`（加/删标签、移组、启停，缺席不改）+ `batch_remove`（面板侧确认摊精确数）；
  ⑥ manifest v3 顶层 `groups` 随包迁移，导入**只补缺不覆盖**（包不能消音主人已写的组话）。
  新码：`group_required` / `group_not_found` / `group_desc_too_long` / `batch_empty` / `batch_noop`；
  `desc_required` 退场（不再能从 add 抬出）；轮 I（v0.10.0）补 `group_exists`
- 面板分类分区视图（v0.8.0 轮 G，纯视图轮）：浏览主形态从 chips+平铺改成“一个分组一个区块”
  （组名·张数 + 一句说明 + 块头“全选本组/编辑说明”就地操作 + 块内网格），“未分组”也是
  区块沉底；搜索跨区块：组名/组说明命中整组都在，否则只留命中图，空区块不出现
  （**“空区块不出现”已被 v0.10.0 轮 I 反转，新边界见陷阱 21**）；
  排序沿用现尺不另发明；刻意不做折叠/不加“试发本组”（主人拍板）。后端入口零变化
- 表情墙（v0.9.0 轮 G-2 → v0.9.1 定稿，纯视图轮，主人对 v0.8.0 卡片的“表单感”反馈而做）：
  格子只露图（auto-fill 128~176px 方块墙；`object-fit: scale-down` 只缩不放——小图被
  放大糊脸是实机钉的坑；左上角极小勾选框与禁用压暗角标是唯二的例外）；点格子在库卡
  顶部展开**聚焦卡**（大图封顶不放大 + 全属性 + 动作排，「编辑」同卡就地切表单，
  再点同格/「返回墙」收起，被删被筛自动收起不留幽灵卡）——不用弹窗的原因见陷阱 20；
  取图共用 `useStickerPreview`（格子=IntersectionObserver 视口懒加载提前 240px、聚焦卡=
  直接排队；同缓存同全局并发尺限同时 2 张；无观察器直接排队宁多拉不漏图；alive 护栏）；
  后端入口/配置/错误码零变化
- **区（v0.11.0 J-1，分类的上层）**：三层「区→分类→图」。区是显式对象（`catalog.json` 顶层
  `zones:[{id,name,desc}]` + `active_zone` + `group_zone`，schema v2；旧库 load 即迁入默认区「自制区」并补写一次盘）；
  区用**内部 id** 引用所以改名白送（`zone_rename`）。分类名**全库唯一（跨区也算）**——她按名字选图，
  一个名字不能有两个家。**她只感知激活区**：`sticker_list`/`sticker_send`（含显式 id）/query 检索/
  awareness 全走 `Library.active_pool()`，非激活区对她整体隐形（陷阱 23）；主人可浏览任意区，
  所有写入（建类/收图/导入）落正在看的区。拆区=连带拆全部分类与图（`zone_remove`，服务端 120s 与
  `LONG_CALL` 同尺；最后一个区不许拆），面板确认摊 zone.total + **3 秒防误删闸**（纯前端，
  服务端不装慢；官方区同样可删，J-2 另留「恢复官方收藏」补种口）。
- **官方区内置（v0.12.0 J-2 P2A，完全内置）**：随包官方收藏 = `official/official_pack.zip`
  （190 张压后 gif，manifest v3，34.8MiB；尺 `core.catalog.OFFICIAL_PACK_RELPATH`，
  打包/播种/入口三处同源）。`on_startup` 调 `Library.seed_official(pack)`：只播一次
  （顶层 `official_seeded` 台账，**只认布尔真**，脏值当未播）；台账只在包干净（rejected+failed=0）
  时盖，半截包下拍重试；入库走 `import_pack` 同一把尺（指纹查重，重放不重入）；
  区的真身尺是 **`builtin` 位不是名字**（同名区收编补位、改名后靠位不认名）；
  **空库才默认激活官方区，旧库不抓台**（陷阱 23 的 `active_pool()` 天然接管未激活的官方区）。
  盘形纯插入：`zones[].builtin` 与 `official_seeded` 只在真时写键，schema 仍 v2。
  面板：tab 官方徽章；官方区不在册且随包在→tab 尾「恢复官方收藏」
  （`zone_restore_official`，force 播种跳台账但照样吃查重；长任务两把尺对齐，陷阱 19 对偶）；
  新码 `official_pack_missing`；播种炸不拦 startup；快照 `official:{pack,zone,seeded}`。
- **分类优先（v0.10.0 轮 I，主人要求重设计分类系统而做）**：分类从“图的附带属性”升为**显式对象**：
  ① **先立分类**——`group_create(name, desc)`（desc 可空，名字必填）。空分类能存住：
  `catalog.json.groups` 里 `{名: ""}` = “在册但未写说明”；**`load()` 不再丢空说明**、
  `set_group_desc("")` 不再删键（清空那句话≠拆掉这个分类，拆它只用 `group_remove`）；
  在册名判定收敛到 `Library.group_names()`（显式 ∪ 隐式）一把尺；
  ② **往分类里收图**——“收一张新表情”表单整块拆掉（逐图字段全归聚焦卡），收图入口下放到
  分类块头（`add(desc="")`，**不再拿文件名当描述**——哈希名会把自己压到分类说明头上）；
  全库共用一个隐藏 `input[type=file]`，目标分类走 ref 传递（`collectTargetRef`）；
  ③ **拆分类 = 连带删图**（主人拍板 1C）：`group_remove(name)` → `Library.remove_group`——
  先改内存、`save()` 成了再删文件（同 `remove()` 纪律，不留暗孤儿）；确认里摊的是
  **服务端张数**（`section.total`），不是搜索筛过的 `rows.length`；服务端 timeout=120s 与面板
  `LONG_CALL` 同尺（陷阱 19）；
  ④ 逐图归类从自由输入升为 **Select（只列已有分类 + 未分组）**，聚焦卡与批量条共用
  `categoryOptions()`——手打新名字会静默立一个没说明的隐式分类，把“先分类后收图”戳穿；
  ⑤ 面板术语统一叫“分类”（只动面板文案；模型面目录格式 `套图：G`/【套图分类】有测试钉着，本轮不动）。
  新码：`group_exists`（重名——含名字已被图住着的情况，那种该去「编辑说明」）；`group_not_found` 复用。
  **不做**（主人拍板 3A）：分类改名 `group_rename`（牵动批量改写 + 说明迁移 + 台账语义，单独立轮；
  现阶段改名 = 新建分类 + 批量移入）
- **面板架构（v0.10.1 拆分轮，纯架构；v0.10.2/v0.10.3 两张卡退场后仍适用）**：`ui/panel.tsx` 只留装配骨架（顶栏 + 卡片摆位），
  其余落位——`ui/shared.ts`（类型/常量/纯函数：两处以上共用的尺；J-1 加 ZoneInfo，
  `buildSections`/`categoryOptions` 按区筛）、
  `ui/preview.ts`（预览缓存 + 懒加载调度 + `useStickerPreview`）、
  `ui/library_model.ts`（库卡动作模型 `useLibraryModel`，无 JSX；J-1 加 viewZone 与区动作一把尺 `zoneAction`）,
  `ui/components/**`（tile/focus/awareness/batch/section/toolbar/zone_bar 七块；usage 台账卡于 v0.10.2 从面板退场，
  后端 `history` 入口与 `usage.json` 保留——台账的正职是跨轮去重与排序，不是展示；
  v0.10.3 文案/操作面：库卡改名「管理表情包」、直传按钮改短「导入」、收件箱按钮与路径提示退场）。
  多文件纪律：相对导入只写 `./shared` 这类简单具名导出（链接器拒 re-export/`export list`），
  运行时依赖账在 32 文件 / 512 KiB 内；新文件的 `t()` 键由 i18n 门的 `ui/**` 递归扫兜住；
  带 `key={...}` 的组件 props 必须声明 `key?: string`（hosted-tsx 真跑类型检查）。
- i18n：zh-CN + en（Python `tr()` 与 TSX `t()` 键全部入文件，有门钉着）

## Out of Scope（v0.1.0 刻意不做）

- 宿主 proactive_chat 的**在线 meme 图源链路**（meme_fetcher 抓图）——平台层，插件无 hook，管不到也不该管
- ~~表情包分组/套图~~ —— **v0.2.0 已做**（`Sticker.group` + 面板 chips + 套图包导入导出；跨会话选包规则不做，我们只有一张收藏间）
- ~~定期目录注入~~ —— **v0.2.0 已做**（存在感注入 awareness，见上面能力面与陷阱 16）；
  外部系统那种"改 prompt + 回复流标记解析器"做不了（平台钩子），插件侧等价物就是静默注入 + llm_tool
- ~~工具重注册心跳~~ —— **v0.1.4 已做**（our_life v0.5.0 方案移植，见上面的工具面与陷阱 15）

## Inferred Technical Needs

- plugin.toml：`[plugin]` `[plugin.sdk]` `[plugin.i18n]` `[plugin.ui]+panel` `[plugin_runtime]`(auto_start=true) + 业务段 `[sticker_manager]/.send/.storage`
- 不声明 `[plugin.store]`：持久化走 `data_path` 文件通道（失败是响亮的，规避 store 静默失效坑）
- SDK surfaces：`plugin.sdk.plugin` 唯一门面；`ctx.push_message` / `ctx.images.upload`（仅 entry/tool 里用，lifecycle 不可）
- UI：hosted-tsx；`ImageUpload`/`ImagePreview` 是 kit 现成件；缩略图懒加载走 `preview` action（context 不带图字节）
- 错误码契约：`^[a-z][a-z0-9_]*$` 稳定 ASCII（invalid_image / duplicate_image / sticker_not_found / send_cooldown / not_enabled / sticker_disabled / sticker_too_large / sticker_file_missing / library_io_error / config_unavailable / desc_required（v0.7.0 起退场，add 不再拦空描述） / desc_too_long / image_too_large / image_undecodable / recent_repeat / probability_declined / upload_not_zip / upload_session_unknown / upload_seq_gap / upload_chunk_bad / upload_too_large / upload_empty / upload_write_failed / zone_required / zone_exists / zone_not_found / zone_last（J-1） / official_pack_missing（J-2））

## 已知陷阱（本机/宿主源码核实，改动前先读）

1. `ctx.images.upload()` 会把图**归一成 JPEG**——动图被压平，所以 gif 只走内联，内联不下就如实拒绝（`services/sender.py`）。
2. 角色归属只认本次调用注入的 `_ctx["lanlan_name"]`；`ctx._current_lanlan` 是脏值。
3. `push_message` 的 `submitted=True` ≠ 宿主已消费；lifecycle 里推送会被静默丢弃（本插件只在 entry/tool 里发）。
4. 整条 payload ≤512KiB：`inline_max_bytes` 默认 256KiB 留了 base64 膨胀（4/3）与封装余量。
5. 挂载态目录名必须等于 entry 包名；仓名 `n.e.k.o_plugin_sticker_manager` 永远挂不上，只能复制（release_gate 已代劳）。
6. 三处同源：`core/configuration.py` 默认值 == `plugin.toml` 业务段 == `config.example.toml`（`tests/test_config_docs_sync.py` 钉死）；改配置三处一起改。
7. i18n 键插入必须文本级（json.dump 会重排+CRLF→LF）；TSX 检查器是文本级规则：**裸 `api` 标识符直接拒收**，一律 `props.surface.api` 成员访问。
8. release 门要求挂载副本里有 `tests/test_smoke.py`（manifest 扫描看源码树），且副本不能含高压缩比垃圾目录（.tmpgate 已排除）。
9. 冷却在内存：重启清零是刻意行为，别"顺手"持久化。**去重相反**：它读持久台账
   （"最近发过什么"是事实记忆不是节奏状态），重启仍生效——两个维度别"顺手统一"。
   概率判定缓存在内存（重启重掷，同冷却纪律）。
10. ruff 门跑 `--ignore-noqa`：noqa 注释不作数，E731（lambda 赋值）这类要真的改掉。
11. **文件名→描述清洗只剩一份**（`core.catalog.desc_from_filename`）：轮 I 之前面板还有一份
    镜像 `guessDesc`，而收图不再拿文件名当描述（描述交空串走回落尺）后那份已删。
    收件箱/裸包导入（服务端）仍用它。单张上限 `MAX_STICKER_BYTES`（Python）与面板同名常量
    **仍是两处同数**（iframe 碰不到 Python，这是跨运行时的必要重复）——改规则两边一起改。
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
16. **心跳与存在感注入的联动方向是相反的，别"顺手"统一**（v0.2.0）：
    tool_watch 不随 `[].enabled=false` 冻结（注册韧性=在场性），
    awareness **必须**随总开关冻结（注入=行为链路，关了就该彻底安静）。
    两者共用同一个 60s `on_watch` 拍，但异常各兜各的——一个漏出去会把另一个的表也标黄。
17. **zip 条目名只当"包内定位符"**（v0.2.0）：`safe_member_name` 把名字压平成 basename，
    防逃逸靠的是"落盘永远走 `add()` 服务端发号、从不拿外来名拼路径"——
    若哪天有人"顺手"改成按包内原名写目录，zip-slip 防线当场失效（test_pack 有专门门钉着）。
    另：先查 `ZipInfo.file_size` 再 `read`（zip bomb 纪律）；导入的 id/时间戳/使用数**不继承**
    来源包（否则假时间污染"最近爱用"排序）。
18. **契约测试的 `tr(` 正则会撞 `writestr(` 的尾巴**（v0.2.0）：i18n 键扫描用
    `(?<![A-Za-z0-9_])tr\(` 带词边界；测试与注释里出现 `writestr("...")` 或字面 `` `tr(" `` 都能
    造出假键把门搞红。新增跨行 tr( 调用是合法的（`\s*` 吃换行），别收紧成单行匹配。
19. **面板调用默认只等 30s，服务端入口 timeout 能到 120s——两把尺必须同时对齐**
    （v0.7.1 实机坑）：宿主桥接客户端 runtime.js 的 `api.call` 默认 `timeoutMs=30000`，
    而 import_inbox / export_pack / import_upload_finish 服务端都是 120s——只改服务端等于
    没改：大包导入超 30s 时服务端在继续入库、面板先报“操作失败”，主人重试还会撞进
    已作废的会话。面板侧长任务统一走 `callAction(..., LONG_CALL)`（timeoutMs=120000）；
    新增长任务入口时两把尺一起改，并在 CHANGELOG 记一笔。分块上传的逐块调用是短任务，
    维持默认——别顺手把整面板都改成 120s 把假死藏得更深。

20. **kit Modal 在宿主 iframe 里是平台级残废，面板里一律禁用覆盖层弹窗**（v0.9.1 实机截图
    钉的）：`frontend/plugin-manager/src/components/plugin/hosted/ui-kit/styles.css` 给
    `.neko-page`/`.neko-card` 挂了 `animation: neko-fade-up ... both`，而关键帧终帧是
    `transform: translateY(0)`（非 none）——fill 永久生效，任何带位移关键帧的祖先都会
    成为 `position:fixed` 后代的包含块；再叠 `.neko-card { overflow: hidden }`：
    遮罩只压暗卡片区域、弹窗底部被卡片边界裁没。插件侧无权改平台 CSS（写区纪律），
    正解是换承载：**就地展开（聚焦卡）**，零 fixed 零 overlay。新入口要详情面时同此例。
    （升级 kit 修好包含块前，本约定不变；判据：在卡片里摆一个会弹的东西前先想这条。）

21. **分类的两侧不对称，别“顺手统一”**（v0.10.0 轮 I）：空分类（在册、零张）**必须显示给主人、
    必须对她隐形**。一边：面板区块与 `state.groups` 要带它出（否则建完就消失，等于没建）——
    这把 v0.8.0 的“空区块不出现”反转了，新边界是：本来就没图的分区块在、被搜索筛空的有图
    分区块仍不在。另一边：`format_group_overview`（她的分类目录）、`sticker_list(group=)` 与
    `sticker_send(group=)` 的候选组名**全部只从有图的贴纸算**——她选中一个空分类就是鬼打墙
    （目录里有它、一发回 `group_not_found`）。test_groups.py 的
    `test_empty_category_never_reaches_her` 是这条的防回归门。
    另记：`group_exists` 把“名字已被图住着”也算撞名（不是只查显式表），否则会出现
    “一个名字两张卡”；隐式分类（有图无说明）与新模型共存，不强迫洗库。

22. **删分类的确认数与面板筛后的数不是一回事**（v0.10.0）：`section.rows.length` 是**搜索筛过**
    的行数，`section.total` 才是服务端报的真张数。破坏性确认只能摄 `total`——
    否则主人搜了一个词、看到 2 张，确认“删 2 张”实际删了 30 张。
    （同理：`Library.remove_group` 先 `save()` 成功再删文件，失败回滚内存，
    不留“目录里没这张、盘上还在”的暗孤儿；孤儿文件收尾交给 `repair()`。）

23. **她只感知激活区，这是一把尺不是两把**（v0.11.0 J-1）：目录/发图/检索/awareness
    全部只能从 `Library.active_pool()` 拿图——**新入口碰她的可选面时必须走这把尺**，
    别在调用点各自 `all()` 再手写区过滤（漏一处就是“她发出了看不见的图”）。
    另两条同族边界：① 分类名全库唯一（`group_exists` 跨区也算）——区只限**可见性**，
    不限名；② 区的生死不进配置：`zones` 是库数据（catalog.json），改它不碰三处同源；
    默认区名「自制区」也是数据不是文案，不走 i18n。
    面板侧的镜像尺：主人可看任意区（viewZone），但写入落当前区——两把尺分开，别“顺手统一”。

24. **官方区的真身是 `builtin` 位，台账只认布尔真**（v0.12.0 J-2）：播种/收编/恢复按钮的判据都
    从 `Library.official_zone()` 拿（位优先、同名只当收编线索）——**新代码别拿名字当官方区的身份证**
    （主人改完名之后靠位不靠名）；`official_seeded` 与 `builtin` 都是**库数据不是配置**，不碰三处同源；
    台账盖章只在包干净时（半截包下拍重试），播种走 `import_pack` 同一把尺——**别开第二条入库路**；
    空库才默认激活官方区，旧库升级绝不抓台（主人的激活位是他的决定）。

25. **包的措辞与主人的措辞撞车时，让位要按字段、但"措辞"是一个整体**（v0.13.0 J-3）：
    官方包重打后要把标签下发到老装机，靠的是包带 `pack_version`、库记 `official_pack_version`、
    `Library.refresh_official_labels()` 按**内容指纹**（不是文件名，库里文件名早就是 `<id>.<ext>`）配对。
    两条容易做错的尺：① **别整条跳过**——主人改过 desc 就整条不刷，这张就永远拿不到包里的新分类；
    ② **别只保 desc**——目录正文走 `caption > desc` 的回落尺，保了 desc 却刷上 caption，
    她的话没被覆盖但**永远不露面**，等于消音。所以 `owner_edited` 记的是**字段名列表**，
    碰过 desc/caption 任一 → 两者都归主人，分类与标签照刷。判断全在 `core/labeling.py`
    （纯函数，逐条钉死），`library.py` 只管 IO；写盘失败**不盖版本**，下次启动自然重试。
    另记一条已知约束（未改）：`catalog_limit_for_model=80` 而官方区有 190 张，她平铺目录只露
    最后导入的 80 张（实测「打招呼与冒泡」「呆住与宕机」平铺零露出）——**分类概览就是她的地图**，
    这也是 J-3 把粒度放开的理由；真要让她扫得到全库，得动那把尺或按分类配额轮换。

## Read Context Plan

- `N.E.K.O/.agent/skills/neko-plugin/**`（契约）→ `plugin/sdk/plugin/base.py`、`plugin/core/context.py`（images/push 语义）
- 本仓 `docs/sticker-system-study.md`（外部表情包管理系统机制剖析与分轮移植方案，2026-09-15）
- 同工作区 `n.e.k.o_plugin_our_life`（工程基线与五门）；`plugin/plugins/qq_auto_reply`（sticker 目录注入先例）
- `问题清单/已知问题.md`（本机环境坑）

## Write Workspace

`F:\ai\neko kaifa2\n.e.k.o_plugin_sticker_manager`（独立 Git 仓；宿主仓同级）

**发行形态（2026-09-16 主人拍板）**：不上架插件市场——release.yml 已删；verify.yml 只留
**无自动触发的 no-op 桩**（平台 `check --release` 要求该文件存在才放行，存在性≠接线：
无 push/PR 触发器，dispatch 了也只是空打印，不调官方 reusable workflow）；
push 不再触发任何云端验证，质量链只有本地五门 `tools/release_gate.py`。
分发 = 自行打包 `.neko-plugin` 后由主人从客户端插件中心导入（`neko-plugin install` 已停用）。

## Risk Follow-ups

- **交接（2026-09-18 更新，J 系列进行中）**——下一个接手的人/AI 先读这里：
  - **J-1 已完工 + 实机验收已回账（v0.11.0，提交 `c694f3d` 已推 origin）**：三层「区→分类→图」落地，
    她只感知激活区（陷阱 23）；旧库 load 即迁入默认区「自制区」。
    **✅ 2026-09-18 主人拍板：0.11.0 包已导入并完成区操作验收，无问题**
    （装机态实测复核：catalog schema v2、默认区在册、库零图属刻意清库）。
  - **J-2 已完工（v0.12.0，P2A 完全内置 + P3 只播一次+恢复按钮，2026-09-18）**：
    - 官方包进 payload：`official/official_pack.zip`（190 张压后 gif，manifest v3，34.8MiB）。
      压图流水线在 `sticker_pack_lab/tools/compress_gifs.py`（190/190 达标，85.4→34.7MiB，最大张 249KiB，
      重刀零命中；本机 gifsicle win 构建不认拼接帧选择/逗号列表，减帧走 explode→merge 三段路）。
    - 播种尺/恢复入口/官方徽章/快照三键全落地（语义见能力面「官方区内置」条与陷阱 24）；
      夹具离线对账 7/7（`sticker_pack_lab/tools/verify_spike_fixtures.py`，报告 `out/spike_verify_report.txt`：
      真路 import_pack 首导 190/0/0/0→复导全 dup、动画保真、active_pool 对账）；测试 250→261。
    - **spike 状态**：甲（86MB 封套）✅——主人已实导 `dist\sticker_manager_SPIKE_86MB.neko-plugin`
      （装机态实测 86.4MiB/48 文件解盘完整，插件正常启动、面板可用，2026-09-18）；
      乙（贴尺 gif 实发）**未跑**——并入下方验收清单第③步（官方包入库后直接点名最大的那几张）。
    - **v0.12.0 实机验收清单（待主人回账，详单见本条下方注）**：导真包→首启 `official_seed=seeded`、
      官方区 190 张、新装默认激活、徽章在→实发一张贴尺 gif（=乙补票）→拆官方区→恢复按钮闭环。
      全绿 → J-2 销账转 J-3；任一红 → 按拍板评估转 B。装机态此刻还是 SPIKE 假货（含 51MiB 垫块），
      导真包时自然被覆盖恢复（覆盖导入不重置知情同意，data 不动）。
    - 历史弹药仍在 `sticker_pack_lab/out/spike/`（含 `boundary_pack.zip` 三张贴尺样张，可单独实发）。
    - 注：验收操作细案在 `sticker_pack_lab/out/spike/验收卡_J2spike.md`（乙那张卡按本条更新后的顺序用：
      先真包再谈样张包，样张包若单独导会在官方区外多住三张同指纹——查重尺会拒，不算脏数据但别奇怪）。
  - **J-3 已完工（v0.13.0，2026-09-18）**：官方 190 张**分好 22 类 + 逐张打标**，并补上"标签怎么下发到老装机"
    这把尺（陷阱 25）。打标与打包的**唯一源在 `sticker_pack_lab`**：标签表 `tools/j3_labels_rows_a/b.py`
    + 分类学 `tools/j3_labels_taxonomy.py` → `tools/j3_labels.py` 出草案（`out/j3_draft/labels_draft.json`
    + 人读 `审阅表.md`）→ `tools/j3_build_official_pack.py` 打真包覆盖本仓 `official/official_pack.zip`
    （**改标签必须涨 `PACK_VERSION`，不涨号老装机不动**）→ 对账两把：`tools/j3_verify_draft.py`
    （草案能否导入）+ `tools/j3_verify_upgrade.py`（旧包→新包升级路径，旧包从本仓 git HEAD 取）。
    主人已拍板的口径：粒度"改开"（22 类、宁窄不兜底）、拿不准的按大致意思即可不追求补齐
    （图内原文只填看得清的）。**待实机验收**：见下一条清单（J-2 那份一起跑）。
  - **v0.13.0 实机验收清单（含 J-2 未回账项，待主人跑）**：导 v0.13.0 真包 → 首启 `official_seed=seeded`
    且日志/快照 `pack_version=1` → 官方区 190 张、面板分类墙按 22 类分区、目录正文是梗义不是文件名 →
    实发一张贴尺 gif（= spike 乙补票）→ 拆官方区 → 「恢复官方收藏」闭环 →（老装机态另测：
    先装 0.12.0 播过种，再覆盖导 0.13.0，看日志 `official labels refreshed: refreshed=190`）。
    全绿 → J-2/J-3 一并销账；任一红 → 按拍板评估转 B。
  - **大文件拆分轮（未开工，另立）**：`services/library.py`（1250 行）/ `core/catalog.py`（524 行）/
    `__init__.py`（2007 行）都超本工作区 200 行/文件的纪律线。J-3 的新判断已经另起 `core/labeling.py`
    不再让大文件长肉，但**存量拆分没做**——它要动播种/查重/区三套已封板的尺（陷阱 22/23/24），
    得单独一轮配"零行为变更"门再做，别夹在内容轮里顺手改。
  - **分类改名（轮 I 欠账）**仍未做：主人拍板 3A 单独立轮；区的改名已因内部 id 设计在 J-1 顺手解决。
- **轮 B（VLM 自动标注）挂起（2026-09-15 拍板）**：库改由**预制表情包**供给——主人自己做包，
  每张图的梗义在做包时写进 manifest（轮 A 的 v2 已支持 caption/visible_text 随包迁移，
  导入走收件箱 zip 通道），插件侧能力已就绪零新代码；做包手法与重启条件见
  `docs/sticker-system-study.md` 轮 B'。另：本文档及仓内所有描述对参考来源一律匿名
  （"外部系统"），不指名具体项目——后续轮次保持此纪律。
- ~~llm_tool 注册表在宿主重启后即丢且无自动重注册~~ —— **v0.1.4 已解**
  （`services/tool_watch.py`，our_life v0.5.0 同方案；间隔 300s，与 fc/our_life 同量级）。
- 上传图无内容审核：库是主人手动收藏的，风险面与在线图源不同；若未来开"她自己去网上抓图入库"，必须接宿主 `utils/meme_moderation` 等价物。
- 存在感注入的节奏（3600s/卡、前 5 行）**没有真机基线**（用户 2026-09-15 确认"还没认真测过"）：
  首轮真机验收要看的是"她是否开始自发用 sticker_send"与"上下文有没有被喂腻"，
  再决定往哪个方向调 interval/lines，必要时加"本会话她已用过表情就不注"的去抖。
