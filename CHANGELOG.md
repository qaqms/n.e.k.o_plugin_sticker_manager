# Changelog

## 0.3.0
v0.3.0「她看得懂每张图在回复什么」——轮 A：语义元数据层（学习 astrbot 表情包管理器的数据层，
机制调研见 docs/astrbot-study.md，代码全自写）：

- **梗义 caption（≤300）+ 图内原文 visible_text（≤200）**：`Sticker` 新增两可选字段。
  caption 是"这张图在回复什么、什么上一句会触发发它"（astrbot CAPTION_PROMPT 的核心思想），
  区别于 desc 的"主人给的短标签"；visible_text 只进检索打分、**不上目录行**。
- **目录行一把尺 `Sticker.catalog_body()`**：caption 优先、空则回落 desc——
  `sticker_list` 与存在感注入共用的 `format_catalog_for_model` 单点改动，两处自动同步。
- **打分序升级**：desc(80) > caption 精确(78)/子串(76) > 套图名精确(75) > 标签(70/60) >
  套图名子串(58) > 图内原文(40) > 文件名(30)；文档钦定序"desc > caption > 套图 > 标签"成门。
- **入口面贯通**：add/update 收 caption/visible_text；新稳定错误码 `caption_too_long` /
  `visible_text_too_long`（校验发生在入库前，坏请求不留半张图）；update 的 caption 学 group 语义：
  **空串=清除标注，缺席=不改**。
- **灭掉整类"重建丢字段"雷**：`with_touch`/`with_sha256`/`Library.update` 改 `dataclasses.replace`——
  sha256 回归（v0.2.0 修）的真病根是手工枚举字段，现在加字段不再需要改四处。
- **套图包 manifest v2**：条目随包携带 caption/visible_text；导入侧从不按版本硬拒，
  旧 v1 包（无键）宽松回空；旧 catalog 分片无键同样宽松兼容，无迁移脚本。
- **面板**：编辑弹窗与单张收藏表单补"梗义/图内原文"输入；卡片正文下展示 caption；
  i18n +8 键（zh-CN/en 同步，文本级插入保 CRLF），4 处旧文案"选图唯一依据"改写
  （现在她看到的正文是 caption 优先）。
- 测试 170 → 190：test_catalog 新 TestSemanticFields/TestSearchSemanticFields（打分序四门、
  宽松兼容、拷贝携带新字段防回归、目录行隐 visible_text）；test_entries 新 TestCaptionFields
  （入库/超长拒/只改 desc 不丢 caption/空串清除）；test_pack 新 TestCaptionInPack（随包迁移、v1 旧包容宽松）。
- 本轮**不碰配置**（三处同源不动）；自动标注（VLM）是轮 B，本轮先把数据层地基打好。

## 0.2.0
v0.2.0「她得记得自己有表情」——存在感 + 套图分组 + 库可迁移（机制调研自 astrbot 表情包管理器，代码全自写）：

- **存在感注入（services/awareness.py）**：复用 60s watch 拍（不新增表），从 `bus.conversations`
  找"最近在跟她说话的角色卡"，把「收藏间里有 N 张 + 最近常用前 K 行」以
  `push_message(visibility=[], ai_behavior="read")` 静默注进她的上下文——治"她想不起来有表情"。
  按角色卡的内存时钟自节流（默认 3600s，配置 `[sticker_manager.awareness]`）；空库/没人说话/被拒
  都不推进时钟；永不炸拍（与 tool_watch 同纪律）。心跳=在场性不随业务冻结，注入=行为链路**随**
  `[].enabled` 冻结——两条相反的联动刻意在 docstring 里写死了。
- **调试入口 `awareness_now`**：绕节奏不绕开关，面板"现在注一条"；稳定码 awareness_disabled /
  awareness_no_target / awareness_empty_library。
- **套图分组（Sticker.group）**：一条表情至多一个组（可空=未分组）；检索按"desc 命中 > 套图名命中 >
  标签命中"打分；目录行形如 `[id] 描述（套图：G；标签：a/b）`；面板 chips 过滤 + 编辑框；
  老库无该键宽松兼容（from_dict 回空串）。
- **导出/导入套图包（core/pack.py + Library.export_pack/import_pack）**：`manifest.json + stickers/`
  打 zip 落 `data/library/exports/`；导入走收件箱通道认 `.zip`（有 manifest 吃元数据，裸图包按
  文件名清洗描述）；**zip-slip**：条目名只当"包内定位符"，落盘永远走 add() 服务端发号，
  `safe_member_name` 拒目录/上跳/盘符/隐藏形状；先查 ZipInfo.file_size 再读（zip bomb）；
  超 PACK_MAX_ENTRIES 截断计数。**刻意不兼容 astrbot 的 memes_data.json**（用户拍板）。
- **修回归**：`Library.update()` 重建 Sticker 时曾丢 sha256（每编辑一次就得等下次查重 lazy 回填），
  现在指纹随文件本体走。
- 频控两层分离（参致 astrbot"软提示+硬闸"思想、实现自写）：core 只拼文案，节奏/目标/时钟在
  services；注入文案与 `sticker_list` 目录行同一把尺（format_catalog_for_model 复用）。
- i18n：+34 键（zh-CN/en 同步；文本级插入保 CRLF）；面板存在感卡（状态/注给/下次最快/调试按钮）
- 测试 121 → 170：test_awareness 21 / test_pack 16 / test_v020_entries 13 + 契约门词边界修正
  （writestr( 的尾巴会撞 tr( 前缀，加负向前查）+ config 三处同源并入 awareness 段
## 0.1.4

- **显示名改为「表情包管理」**（只动 `[plugin].name`、面板标题四处与文档题头；
id / entry / 类名 / 仓名不变，装机兼容）——在插件市场里比「表情包管理器」好听得多了
- **补 `plugin.name`/`plugin.description`/`plugin.short_description` 双语 i18n 键**：宿主已安装卡片
  优先读插件语言包的 `plugin.name`（回落才是 toml name），en 用户不再看到中文回落名；
  改显示名今后要两处一起改（已成文进 DESIGN Identity Lock 段）

- 工具注册心跳（移植 our_life v0.5.0 已验证方案，适配本插件两工具面）：

- **`services/tool_watch.py`（新）**：低频巡检器——每 5 分钟回环 `GET /api/tools`
  点名缺席的 `sticker_list` / `sticker_send`，只对"可达且真缺席"补挂（走基类
  `_notify_llm_tool_registered` IPC 重发，replace 幂等；`getattr` 取用，宿主改名
  时降级为日志不是崩溃）；不可达零动作（不盲挂、不每拍追打）；认不出的响应形状
  按"全场无工具"处理（幂等重发比漏挂安全）
- **病因**：`@llm_tool` 只在启动时发一次 IPC；main_server 晚起或重启后注册表全丢
  且不重试，她的发表情通道**静默失效**没人发现（宿主 docstring 自己写着
  "The plugin can re-register later"，官方 tool-calling 文档钦定周期巡检解法）
- **新 timer `on_watch`（60s 一拍，内部按 300s 自节流，首拍即查）**：本插件此前
  无后台拍；timer 无 watchdog，入口层双保险吞异常。心跳与 `[sticker_manager].enabled`
  无关：注册韧性是在场性，不随业务冻结而冻结
- 间隔不进配置：可靠性参数不是行为参数（与冷却只在内存同等待遇）
- 测试 108 → 121（新 `tests/test_tool_watch.py` 13 条：形状差集×4 / 间隔自节流×2 /
  不可达不盲挂 / 点名补挂 / 单名失败不连坐 / protected 面缺席降级 / fetch 异常吞掉 /
  间隔常量钉住 / timer 入口离线可跑）；DESIGN 风险区销账 + 陷阱 15

## 0.1.3
实机首测修复（Steam 宿主日志钉的坑）：

- **大图预览超时（真 bug）**：宿主 entry 回包走 ZeroMQ 控制通道，单帧硬上限
  4,784,128 字节；3.95MB 图的 dataUrl（base64 后 5.27MB）被传输层拒发，
  宿主干等到 15s 超时→面板只剩一句 500。preview 改为**分段协议**
  （offset / chunk_base64 / next_offset / done，宽 3MiB 且是 3 的倍数，
  base64 段无填充可直接串接），任意 ≤8MiB 的图都能拼回预览；面板逐段拉取
- **失败不再静默**：卡片上的发送/编辑/禁用/删除出错现在在卡内出 Alert，
  稳定码带中文文案（`panel.error.*` 十八码全盖）：send_cooldown 这类
  正常拦截终于能看懂“刚发过一张，让她缓一下”
- 新增 i18n：18 个 panel.error.* + fields.offset；preview 描述重写
- 测试 106 → 108（分段拼回/越界钳位/脏 offset 三道门）；
  DESIGN 陷阱 14 把控制通道 vs 媒体通道的区别成文

## 0.1.2
批量导入（思路参致 astrbot 表情包管理器，代码自写）：

- **面板多选批量收藏**：原生 `<input type="file" multiple>` + `FileReader` 逐张走
  已测的 `add` 通道（魔数/查重/体积限制全复用）；描述取自文件名清洗，
  单轮上限 64 张（超出如实报数），进度与四类计数（已收/重复/超限/失败）
- **收件箱目录**：新增 `data/library/inbox/`——把图片文件拖进去，面板点
  「导入收件箱」（`import_inbox` 入口）服务端逐张入库；成功/重复的源文件删掉，
  超限/坏图保留原地可重试；隐藏项不碰；面板按钮带待收计数，上下文露目录路径
- 文件名→描述清洗规则在两处各一份（`core.catalog.desc_from_filename` /
  面板 `guessDesc`）：跨运行时碰不到，改规则两边一起改（DESIGN 陷阱 11）
- 单张上限 8MiB 抽成 core 常量 `MAX_STICKER_BYTES`，入口与收件箱共用
- 测试 95 → 106（core 清洗 5 / 持久层收件箱 4 / 入口层 2）

## 0.1.1
基础完善轮（参考 astrbot 表情包管理器，只取纯管理面的小切口）：

- **重复入库拦截**：每张图入库时记 sha256 内容指纹，同图再传返回稳定码
  `duplicate_image`；旧版无指纹的条目在查重时 lazy 回填（三件里唯一碰数据的改动，
  幂等、宽松兼容，catalog schema 不变）
- **库体检与自修复**：新 `repair` 入口 + 面板按钮——清掉文件已丢的条目、
  删掉没人引用的孤儿文件、回填旧条目指纹，返回三项计数
- **面板收藏体验**：文件名预填描述（改过就不覆盖）；成功/失败（含 duplicate）
  都有 Alert 反馈，不再只进 console
- 测试 86 → 95（查重/回填/repair 各有入口层与持久层两道门）

## 0.1.0

首个版本：能收能发。

- 表情库持久化：`data/library/` 下 JSON 目录 + 图片文件 + 使用台账（写穿式、原子替换）
- 管理入口：add / update / remove / send / list / preview / history / switch（全部 `@ui.action` 暴露给面板）
- LLM 工具：`sticker_list` + `sticker_send`，她在对话里自主挑图发送
- 发送链路：≤256KiB 内联（gif 保动画）、大图换 URL、按角色卡冷却 20s、失败也进台账
- Hosted TSX 面板：收藏表单（ImageUpload）、网格浏览（懒加载预览）、编辑/禁用/删除、使用记录
- fail-closed 总开关 `[sticker_manager].enabled`（默认 false）
- i18n：zh-CN / en；三处同源门（configuration == plugin.toml == config.example.toml）
- 工程：五门 release_gate（pytest / ruff / check / release / hosted-tsx），本机 TEMP 对策已内置
