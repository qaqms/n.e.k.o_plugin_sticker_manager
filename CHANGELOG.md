# Changelog

## 0.10.1

v0.10.1「面板拆分」——纯架构轮（主人选定方向：表情包管理类问题与面板整体管理的地基）：`ui/panel.tsx` 从 1924 行收敛为 273 行**装配骨架**，其余按 our_life v0.6.0 的拆分先例落位，**行为零变化、后端零变化、错误码零变化**：

- **文件落位**（house style 对齐 our_life：shared + components/**，只用具名导出，
  单绑定期声明形态——链接器拒 re-export/`export *`/导出列表）：
  - `ui/shared.ts`——类型（StickerRow/Section/State/Surface…）+ 常量（MAX_BATCH_FILES /
    MAX_STICKER_BYTES / LONG_CALL）+ 纯函数（extractCode/formatTime/readAsDataUrl/
    dataUrlToBase64/callAction/categoryOptions/buildSections）；
  - `ui/preview.ts`——预览缓存、并发尺限 2 的懒加载调度、IntersectionObserver 共用观察器、
    `useStickerPreview`（格子与聚焦卡仍共用一把尺，分段协议不变——陷阱 14）；
  - `ui/library_model.ts`——库卡动作模型 `useLibraryModel(surface)`：搜索/勾选/批量、
    收图 ref 纪律（轮 I）、直传分块、导入导出体检、建类/删类/组说明编辑全部搬入，
    不放任何呈现 JSX；
  - `ui/components/`——sticker_tile / focus_card / usage_card / awareness_card /
    batch_bar / category_section / library_toolbar 七块（awareness 的 ping 与调试反馈
    自治进卡内——与库卡动作没有对偶，不硬并进 model）。
- **i18n 契约门同步扩扫**：`tests/test_i18n_contract.py` 从 `ui/*.tsx` 顶层 glob 改为
  递归扫 `ui/**` 的 tsx+ts——拆分后键分散在新文件里，不扩面等于让新文件的键绕过门
  （our_life 同名门的 rglob 同款思路）。`categoryOptions` 的参数从 `translate` 归一为 `t`，
  让它的 `panel.group.none` 重新进门扫描面。
- **一处小形态修正**：`CategorySection` 的 props 声明补 `key?: string`（hosted-tsx 真跑
  类型检查，`key={section.key}` 要有槽位——本仓 StickerTile/FocusCard 同款先例）。
- 依赖账：运行时 11 文件 ≪ 32 文件 / 512 KiB 上限（hosted-tsx 门实测通过）；
  发行包 payload 含全部新文件（实测列包）。
- 验证：pytest **242 passed**（门数不变）；五门全绿
  `check -r` `payload_hash_verified=True`。

## 0.10.0

v0.10.0「分类优先」——轮 I：分类从「图的附带属性」升成**显式对象**（主人要求重设计分类系统；本轮动后端，不是纯视图轮）：

- **先立分类，再往分类里收图**：新入口 `group_create(name, desc)`（名字必填、说明可留空）与
  `group_remove(name)`；空分类从此存得住——`catalog.json.groups` 里 `{名: ""}` 的含义改成
  「在册但未写说明」，`load()` 不再丢空说明、`set_group_desc("")` 不再删键
  （**清空那句话≠拆掉这个分类**，拆它只用 `group_remove`）。在册名判定收敛到
  `Library.group_names()`（显式 ∪ 有图在住的隐式）一把尺，入口层与创建查重共用。
  新错误码 `group_exists`：重名（**含名字已被图住着**——那种情况主人该去「编辑说明」，
  否则会一个名字两张卡）；`group_not_found` 复用于删不存在的分类。
- **删分类 = 连带删它里的图**（主人拍板 1C）：`Library.remove_group` 先改内存、`save()` 成了再删文件，
  失败回滚内存（同 `remove()` 纪律，不留「目录没这张、盘上还在」的暗孤儿，孤儿收尾仍归 `repair()`）；
  服务端 `timeout=120` 与面板 `LONG_CALL` 同尺（陷阱 19）。**不做改名**（3A 拍板：牵动批量改写 +
  说明迁移 + 台账语义，单独立轮；现阶段改名 = 新建分类 + 批量移入）。
- **面板：「收一张新表情」整卡拆掉**（AddForm 249 行离场）——四个逐图字段与「收进表情库」按钮
  在这张卡里是主人用起来最累、收益最低的一段：逐图编辑聚焦卡早就有（v0.9.1），
  整批标签/分组批量条也早有（v0.7.0 `batch_update`）。台账卡因此升占整行。
- **收图入口下放到分类块头**：「收图进这一类」多选文件，目标分类就是这一块（全库共用**一个**隐藏
  `input[type=file]`，目标走 `collectTargetRef` 传递——点按钮→`click()`→`onChange` 三步里闭包
  可能抓到旧 state，ref 当场生效）。想逐图补什么，收完点开图在聚焦卡里改。
- **收图不再拿文件名当描述**（行为变化，如实记）：`add(desc="")`，目录正文按轮 F 那把尺回落到
  分类说明。为什么：外部包里哈希名一堆（`0FFD1AFA…jpg`），文件名描述会把自己压到主人写的分类说明
  头上。面板镜像函数 `guessDesc` 随之退场，陷阱 11 从「两份一起改」改成「只剩 Python 一份」。
- **逐图归类 Input → Select**：聚焦卡与批量条两处共用 `categoryOptions()`（选项 = 未分组 + 已有分类），
  不再能手打新分类名——自由输入会静默立一个没说明的隐式分类，把「先分类后收图」戳穿。
- **空分类显示给主人、对她隐形**（陷阱 21 成文；反转 v0.8.0「空区块不出现」）：面板区块与
  `state.groups` 带它出（否则建完就消失等于没建），而 `format_group_overview`、
  `sticker_list(group=)`、`sticker_send(group=)` 的候选组名全部只从**有图的**贴纸算——
  她选中一个空分类就是鬼打墙。新边界：本来就没图的分区块在，被搜索筛空的有图分区块仍不在。
  破坏性确认摊的是服务端张数 `section.total` 而不是搜索筛过的 `rows.length`（陷阱 22）。
- 面板术语统一叫「分类」（只动面板文案；模型面目录格式 `套图：G`／【套图分类】有测试钉着，本轮不动）。
- i18n +30 键（zh-CN/en 同键集，文本级族内插入）；若干值改写（术语统一、`group_set_desc` 的空串语义订正、
  `plugin.description` 双通道同步）；退场键按仓内先例留档不删（`panel.card.add`、`panel.add.*`、
  `panel.batch.label/busy/done`、`panel.group.label`、`panel.batch.group_ph`）。
- 测试 **235 → 242 passed**：`tests/test_groups.py` 新 `TestCategoryLifecycle` 八条门（空分类在册且
  活过重载、两类撞名、建完即可写说明、入口形状与 `state.groups` 的 `count: 0`、删空分类、
  删分类连带删图与文件——数目录也数磁盘文件、**空分类不进她的目录也发不出**的防回归门），
  另把两条旧语义门改正（`set_group_desc("")` 保留键、poisoned 段空说明保留键）。
- 五门全绿（pytest / ruff / check / release / hosted-tsx），`check --release` `payload_hash_verified=True`。

## 0.9.1

v0.9.1「墙不糊脸、弹窗不再被裁」——实机截图钉出的两只观感雷（纯视图轮，后端零变化）：

- **拉伸感真凶：小图被放大展示**。测试库里原图只有 160×160，v0.9.0 的 6 列墙在 2K 窗口
  里每格≈365px，浏览器硬放大 2.3 倍糊脸。改：格子 `object-fit: scale-down`（只缩不放，
  小图按原尺寸摆、四周留底色边）；墙改 `auto-fill minmax(128px, 176px)` 自适应方块轨道，
  不再随窗口宽度无限长大；聚焦卡大图同样不放大（封顶 420×380，小图原尺寸清晰）。
- **弹窗被关进卡片里（平台级坑，DESIGN 陷阱 20 成文）**：kit 的 .neko-page/.neko-card
  挂着 `animation: neko-fade-up ... both`，关键帧终帧 `transform: translateY(0)` 非 none、
  fill 永久生效→祖先成了 position:fixed 的包含块；再叠 .neko-card 的 overflow:hidden，
  实机表现为：遮罩只压暗表情库卡、弹窗底部被卡片边界裁没。**插件侧修不动平台 CSS，
  改用「聚焦视图」根治**：点格子→库卡顶部就地展开**聚焦卡**（大图+全属性+动作排，
  「编辑」同卡就地切表单，零 overlay 零 fixed，任何库长度免疫）；再点一次同一格或
  「返回墙」收起；被删/被筛掉自动收起不留幽灵卡。看做分离语义不变，只换承载。
- 取图逻辑抽成共用 hook `useStickerPreview`（格子=视口懒加载+排队，聚焦卡=直接排队；
  同缓存同并发尺；alive 护栏防卸载后 setState）；kit Modal/ImagePreview 退场（import 清干净）。
- i18n +2 键（panel.focus.title/back）。

## 0.9.0

v0.9.0「表情墙」——轮 G-2：格子去表单化，参数全部收进详情弹窗（纯视图轮，后端零变化）：

- **外面只剩图**：卡片从“横躺的表单”改成方形图块（6 列墙）：零文字零按钮；
  仅保留两样——左上角极小勾选框（批量刚需，主人拍板）、禁用态整块压暗+“已禁用”角标；
  选中态图块描蓝边。
- **id/发出次数/最近发出/描述/梗义/图内原文/分组/标签/入库时间 + 发到聊天/编辑/禁用/删除**
  全部搬进**点击弹出的详情弹窗**（看做分离：弹窗=大图+全属性+动作排；点「编辑」再开
  第二层编辑表单，那张表单本身没改）；弹窗内动作失败在弹窗里报，弹窗关着时才在图块下报。
- **预览改自动懒加载**（拍板 ③A）：IntersectionObserver 滚进视口（提前 240px）才拉图，
  叠一把**全局并发尺限同时 2 张**——单张预览是分段的串行调用，几百格同时开跑会踩挤
  插件子进程（原“点按钮才加载”就是为防这个，现在换了更精确的闸）；拿不到观察器环境
  直接排队兜底，宁多拉不漏图；预览缓存/失败记空串不重试风暴的既有纪律不变。
- i18n +10 键（zh-CN/en 尾部纯插入）；`panel.thumb.load`/`panel.batch.pick` 退场留档。

## 0.8.0

v0.8.0「收藏间有了楼层」——轮 G：面板分类分区视图（纯视图轮，后端零改动）：

- **浏览主形态从“chips 过滤 + 平铺大网格”改成“按组分区”**：一个分组一个区块
  （组名·张数 + 一句组说明 + 块内网格），一路滚下去就是她的收藏间目录；
  “未分组”也占一个区块沉底，天然暴露还有多少张没归家。
- **操作入口上到区块头**：全选本组（再点取消）、编辑说明（就地一行，同一时刻只开
  一个编辑态）；原 chips 排与 chips 下的说明输入框退场——分区后它们是冗余控件。
- **搜索语义升级**：跨区块过滤，组名/组说明命中→整组都在，否则只留命中的图；
  空区块不出现；有库但筛选无命中时给“换个词试试”的空态（与空库文案分开）。
- 区块内卡片、批量条、直传导入、台账、存在感卡等其余功能面**全部不动**；
  排序沿用现有尺（组间按目录同序字母、组内按库序），刻意不加折叠、不加“试发本组”
  （主人拍板：几百张内一路滚够用，分区头不另升发送入口）。
- i18n +8 键（zh-CN/en 同步，文本级尾部插入）；后端入口/配置/错误码零变化。
- 本轮占用“轮 G”序号：两步预检候选顺延为轮 H（study §5 同步改名）。

## 0.7.1

v0.7.1「大包装不再报假死」——面板超时尺对齐（迟到的审计抓到的一只真雷）：

- 宿主桥接客户端默认只等 30s（runtime.js 实测），而 import_inbox / export_pack /
  import_upload_finish 服务端都是 timeout=120——大包导入超 30s 时服务端在继续、
  面板先报失败，主人重试还可能撞进半成品会话。现在 `callAction` 支持 options，
  这三条长任务统一传 `{timeoutMs: 120000}`；短任务（含逐块上传，单块毫秒级）维持默认。

## 0.7.0

v0.7.0「打标不折腾」——轮 F：对齐参考系统的“分类=描述”心智，把逐图文本从门槛降为可选：

- **逐图 desc 不再必填**：空描述也能收——目录行正文回落链变成
  `caption > desc > 分组说明 > 「套图「G」里的一张」> 「未标注」`（一把尺，缺口如实标出来而不是造假描述）；
  超限仍拦（desc_too_long），update 传空串 = 清除。`desc_required` 自本版本退场（契约表已标）。
- **分组说明（catalog.json 顶层 `groups`）**：`group_set_desc` 入口/面板 chips 下一行编辑框——
  一句“什么时候用这一组”就是她看到的分类目录正文（≤300；只能给有图在用的组写，空组存不了图）。
- **两级目录**：`sticker_list` 无搜索词先回【套图分类】再回【条目】；`group=` 只看那一组。
  存在感注入同步：分组概览插在指南与常货之间，行形状与 sticker_list 同一把尺。
- **组内选图**：`sticker_send(group=)` ——她只拍板“这一组调性对”，哪张由系统定：
  候选池先过同一把近期去重尺再随机（剔到空→`recent_repeat`），撞多个组名回
  `group_candidates` 让她二决；id > group > query 优先级；节奏三闸（去重/概率/冷却）照常把关。
  随机尺 `_GROUP_PICK` 模块级可注入（对齐 _RNG 纪律）。新工具面拒因：`group_not_found` /
  `group_empty` / `group_candidates`。
- **批量整理**：`batch_update`（加/删标签、移组、启停，缺席不改）与 `batch_remove`；
  面板每张卡加勾选框，选中后弹批量条；删除前先把**精确张数**摊在确认里（参考系统的破坏性确认）。
  新码：`batch_empty` / `batch_noop`。
- **manifest v3**：顶层 `groups` 随包迁移；导入**只补缺不覆盖**——包里组话不能消音主人已写的；
  旧包（v1/v2）无此键自然回空，宽松兼容。导出面同步携带。
- 测试 218 → 235：回落链/分组概览形状/库层持久+毒数据/入口守卫/批量三面/组内选图五面
  （钉选、去重逐轮排池、force 放池、同名歧义、id 优先）/两级目录/manifest v3 随包+不消音。
- 顺手修掉本轮改造时 LSP 在既有代码上抓到的两处：awareness 时间戳的巨整数
  OverflowError（与轮 D 配置加固同纪，坏时间戳当 0 不炸整拍）、bus 回包鸭子形状的
  Any 标注（行为不变，静态跟得住）。

## 0.6.0

v0.6.0「导入不再找隐藏文件夹」——轮 E：面板选择文件直传套图包：

- **背景（真实踩坑）**：收件箱是唯一包入口，但运行时数据目录藏在 AppData 深处，
  用户把 zip 放在显眼的构建目录里点「导入收件箱」×4 全部空转——“导入”应该是
  选文件，不是“服务端读一个主人找不到的目录”。
- **新链路**：面板「选择套图包导入」→ 原生文件选择器（accept `.zip`）→ 分块直传。
  三个新入口 `import_upload_start`（开会话，回 session + 每块原始字节上限）→
  `import_upload_chunk`（按 seq 从 0 连续追加 base64 块）→ `import_upload_finish`
  （收尾走 import_pack 同一把尺，回四类计数）。面板进度/结果文案复用 inbox 同款报告形状。
- **为什么分块**：面板→entry 的 args 与预览回包同受 ZeroMQ 单帧上限 4,784,128 字节
  （陷阱 14 的反方向），几 MiB 的包整块 base64 会被直接拒；块大小由服务端 start
  回包定（3MiB 原始→≈4MiB base64，封套留余量），面板不硬编码。
- **会话纪律**：只存本进程内存 + `data/uploads/.sid.part`；重启即作废，重选文件即可
  （不做断点续传：不值得为短命交互引入复杂度）；seq 乱序/超限/写失败一律**作废会话**，
  不静默拼接；finish 后暂存体无论成败都删；新开会话前顺带回收过期死体（内存会话按 at、
  盘上残留按 mtime，24h）。名字带路径形状直接拒（`upload_not_zip`），不采纳
  safe_member_name 的“剥目录”宽容——面板传来的就该是裸文件名，诚实 > 宽容。
- **收件箱降为高级旁路**：入口保留（脚本化批量/无面板场景仍可用），面板提示文案
  改为首选选择器。导入的图片多选通道不变。
- 顺手修一个真雷（lens 在改动文件上抓到的既有缺口）：`Sticker.from_dict` 的
  时间戳/计数字段改用 `_lenient_float/_lenient_int`——手改坏的 `catalog.json` 过去会把
  整本库打成不可加载（load 的条目循环没有逐条 try，宽松承诺必须在 from_dict 内兑现）；
  nan/inf/巨整数/类型错全部回 0，好值不受牵连，钉 1 个专项测试。
- 新稳定码七个：`upload_not_zip` / `upload_session_unknown` / `upload_seq_gap` /
  `upload_chunk_bad` / `upload_too_large` / `upload_empty` / `upload_write_failed`（双语 i18n 同步）。
- 测试 207 → 218：会话规则十门（拒非 zip/manifest 包全链路小块多块/乱序作废/超限作废/
  空会话/假 zip 诚实计败/过期回收/入口往返/入口参数守卫/实例隔离）+ from_dict 毒数值一门。

## 0.5.0

v0.5.0「她的发送有节奏」——轮 D 前半：跨轮去重 + 概率闸门（学习 外部系统的节奏纪律，
机制见 docs/sticker-system-study.md；它没有的持久台账是我们能做得比它好的地方）：

- **跨轮去重（轮 D①，`[sticker_manager.send].recent_dedup_count`，默认 5，0=关）**：
  同一角色卡最近 N 张**成功发出**的图在"她自主选图"时不再出现——query 候选池先剔除再
  判定（剔到空但原池有命中→如实回 `recent_repeat`，和 `no_match` 分开：前者让她换一张，
  后者让她换词）；显式 id 在发送层被同一把尺拦下（tool 以外没有第二条能绕的通道）。
  数据源是**持久台账**："最近发过什么"是事实记忆，重启不该失忆——与冷却（内存表，
  陷阱 9）是两个维度。面板"试发"（source=panel）不拦：那是主人的直接动作。
- **概率闸门（轮 D②，`probability` 默认 1.0=关，`probability_reuse_sec` 默认 60）**：
  她想发图时按概率放行，未中则该轮回落纯文字（`probability_declined`，台账可见）。
  **掷一次存判定、复用窗口内不重掷**——multi_candidates→拿 id 二次定夺是同一次意愿的
  延续，不配第二次骰（外部系统的 p² 教训直接继承）；判定缓存是内存表，重启重掷。
  默认关：闸门会拒绝她已下的决定，属行为干预，主人显式打开才生效（与参考系统
  probability=100 默认同哲学；总开关仍是 fail-closed 那道）。
- **`force` 绕行（新工具参数）**：主人点名要再看某张时带 `force=true`，同时绕过去重与
  概率闸；**冷却不绕**——那是防刷屏不是表达问题。
- **软提示同层告知**：awareness 中段新增"刚发过的会被挡/被拒了就文字回、别重试"，
  尾段新增 force 通道告知；`sticker_send` 工具描述与参数说明同步；两个新拒因码带
  面向模型的二段指引 hint（同 multi_candidates 纪律，不进 i18n）。
- 新稳定码两个：`recent_repeat` / `probability_declined`（进台账，面板错误面可翻译，
  双语 i18n 已补；台账失败行列改为优先翻 `panel.error.<code>`、无键仍直出码）。
- 面板 dashboard 的 config 可观测面补三个新键；工具层失败尝试照旧进台账
  （"她想发但没发出去"多了两个节奏原因）。
- 配置加固（顺手钉的真雷）：`_as_int`/`_as_number` 现在对 TOML 合法的
  `nan`/`inf`/巨整数字面量全部钳位回默认不抬异常（int 域不做 float 中转）。
- 测试 194 → 207：概率闸五门（默认短路不掷/未中拦投递/窗口内不重掷/过窗重掷/
  force 只绕双闸不绕冷却）+ 去重四门（成功计数失败不计数/角色卡隔离+跨实例持久/
  0=关/query 候选剔除后严格最优直发）+ 工具面三门 + 文案门追加三句 + 三处同源键更新。

## 0.4.0

v0.4.0「表情是她挑的，不是系统拍板」——轮 C：按情境选图（学习 外部系统的选图纪律，
机制见 docs/sticker-system-study.md）：

- **`resolve_send_target` 判定器（core）**：query 选图不再"发匹配第一张"——
  最优分严格唯一→直发；头部并列→回 top-K 候选清单（`multi_candidates`，
  上限 5 与 外部系统 top_k 同量级）让她拿 id 发第二刀。**不替她拍板并列**。
- **检索一把尺重构**：`search_with_scores` 成为唯一实现，`search_stickers` /
  `resolve_send_target` 都是薄封装（打分/排序/目录行三处同源更牢）。
- **收紧空枪**：id 与 query 都不给→`id_or_query_required`（旧行为会"顺手"发常货——
  选图必须有依据，那是"发得准"的另一半）；点到的 id 失效仍如实回 `no_match`。
- **注入文案升级（学 外部系统提示词的 head 段 的"使用规则"段）**：中段新增——
  "分清你是在安慰对方还是在说自己，拿不准、不贴切就不发；一条回复配一张就够，
  宁缺毋滥"；尾段同步候选机制。**频控两层分离**：软提示进文案，硬闸在发送层冷却（既有）；
  "每条回复最多 N 张"的跨轮计数**刻意不做**——冷却已是硬闸，另立台账不值。
- 工具描述同步：`sticker_send` 的 query 参数明说"会按梗义筛、多候选回清单"。
- 本轮**不碰配置不碰面板**（三处同源不动；候选面只在工具链路，面板直发仍走 id）。
- 测试 190 → 194：并列回候选×2（含"一发都没出"与二刀直发）、严格最优不吃候选规则、
  空枪拒绝、注入文案使用规则门。

## 0.3.0

v0.3.0「她看得懂每张图在回复什么」——轮 A：语义元数据层（学习 外部表情包管理系统的数据层，
机制调研见 docs/sticker-system-study.md，代码全自写）：

- **梗义 caption（≤300）+ 图内原文 visible_text（≤200）**：`Sticker` 新增两可选字段。
  caption 是"这张图在回复什么、什么上一句会触发发它"（外部系统的标注提示词 的核心思想），
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

v0.2.0「她得记得自己有表情」——存在感 + 套图分组 + 库可迁移（机制调研自 外部表情包管理系统，代码全自写）：

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
  超 PACK_MAX_ENTRIES 截断计数。**刻意不兼容 外部系统的 memes_data.json**（用户拍板）。
- **修回归**：`Library.update()` 重建 Sticker 时曾丢 sha256（每编辑一次就得等下次查重 lazy 回填），
  现在指纹随文件本体走。
- 频控两层分离（参致 外部系统"软提示+硬闸"思想、实现自写）：core 只拼文案，节奏/目标/时钟在
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

批量导入（思路参致 外部表情包管理系统，代码自写）：

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

基础完善轮（参考 外部表情包管理系统，只取纯管理面的小切口）：

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
