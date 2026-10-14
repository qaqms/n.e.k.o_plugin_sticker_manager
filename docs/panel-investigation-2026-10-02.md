# 插件面板调查与优化建议

调查日期：2026-10-02。调查基线版本：0.20.15；实现版本：0.20.16。

本文第 1-8 节保留最初“先调研清楚”阶段的结论与建议，描述的是 0.20.15 基线，
不是 0.20.16 的现状。后续根据主人补充的“底部点图后还得滑到顶部看详情”需求，
已完成独立详情视图与隔离构建，实际范围和验收见第 9 节。
全过程没有修改宿主源码、Steam 安装、用户配置或正式表情库。

## 1. 开发边界与项目形状

- `F:\ai\neko kaifa5\N.E.K.O` 是本地宿主源码，只作为 SDK、协议和布局行为的只读参考。
- `F:\ai\neko kaifa5\n.e.k.o_plugin_sticker_manager` 是唯一业务改动范围。
- 插件 ID 为 `sticker_manager`，Python 入口为 `StickerManagerPlugin`。
- `core` 处理目录、配置、分类、包、缩略图等逻辑，`services` 维护库、发送、提醒、
  话轮观察与工具注册状态，`__init__.py` 装配入口与面板快照。
- UI 为 Hosted TSX：`panel.tsx` 装配视图，`library_model.ts` 维护库动作与状态，
  `preview.ts` 负责图像缓存和队列，七个组件负责具体呈现。
- 模型通过 `sticker_list` / `sticker_send` 选图。面板优化无需改发图工具协议、
  积极度判据、提醒门槛、冷却、去重、概率或数据 schema。
- 参考项目仅借鉴分类目录、管理操作分层、批量选择及图片详情的交互组织；
  不照搬其独立 WebUI、弹窗 CSS、模型钩子或资源协议。

## 2. Steam 实际环境

| 用途 | 位置 |
| --- | --- |
| Steam 安装目录 | `E:\steam\steamapps\common\n.e.k.o` |
| 运行状态记录 | `C:\Users\ms\AppData\Local\N.E.K.O.runtime\launcher.json` |
| 已安装插件代码 | `C:\Users\ms\AppData\Local\N.E.K.O\.neko-plugin-installations\plugins\sticker_manager` |
| 用户插件配置 | `C:\Users\ms\AppData\Local\N.E.K.O\plugins\sticker_manager\config` |
| 正式表情库 | `C:\Users\ms\AppData\Local\N.E.K.O\plugins\sticker_manager\data\library` |
| 日志根目录 | `C:\Users\ms\AppData\Local\N.E.K.O\logs` |
| 插件专属日志 | `logs\plugin\N.E.K.O_Plugin_sticker_manager_20261002.log` |
| 宿主插件日志 | `logs\N.E.K.O_Plugin_20261002.log` |
| 主聊天日志 | `logs\N.E.K.O_Main_20261002.log` |
| 后台任务日志 | `logs\N.E.K.O_Agent_20261002.log` |

Steam 运行记录的 main/tool/plugin server 端口分别为 48911 / 48915 / 48916。
本轮未打开正式管理面板，也未向正式插件提交管理动作。

开发仓与已安装插件的 11 个 UI `.ts/.tsx` 文件逐一比较 SHA-256，全部一致。
因此已排除“安装版仍是另一份旧 UI”这个解释。

Steam Hosted UI runtime 内嵌于：
`resources\bin\frontend\plugin-manager\dist\assets\PluginDetail-DDaUCTpr.js`。
其 raw style 处理（1719-1732 行）与本地 runtime 的关键行为一致。
只验证了相关代码段，不声称两个完整宿主构建完全一致。

10 月 2 日插件日志中统计到 138 次 preview trigger，未检出预览相关
ERROR/WARNING/timeout/failed/file_missing/image_unreadable。
这不能证明所有请求均成功，但现有日志没有支持“图片获取失败”的证据，
也无法证明浏览器的裁切、遮挡或层叠根因。
调查未记录聊天正文、API key 或本机桥接令牌。

## 3. 调查基线的点击图片机制

源码链路：

1. `ui/components/sticker_tile.tsx:61` 的 `onClick` 调用 `onOpen`。
2. `ui/panel.tsx:250` 只切换 `lib.focus`。
3. `ui/panel.tsx:185` 在整面分类图墙之前插入 `FocusCard`。
4. `ui/panel.tsx:239` 之后仍继续渲染全部分类和缩略图。
5. 详情使用原图；格子使用 256px 首帧缩略图，GIF 在详情里才播放。

当前不是全屏放大弹窗，也不是覆盖整面图墙的预览层。
不能将这次现象直接归因为“弹窗 z-index 不够”，更不能只加大 z-index 当作修复。

## 4. 已证实的缺陷

### 4.1 原生 style 数字长度被忽略

宿主 Hosted TSX 不是 React DOM。
`N.E.K.O/frontend/plugin-manager/src/components/plugin/hosted/ui-kit/runtime.js:612`
的 `setProp` 将原生 style 值直接 `String(value)` 后赋给 `dom.style[key]`。
只有 UI kit 组件的 `cssSize` 等辅助逻辑会补 px。

因此 `<Stack gap={8}>` 正常，但 `<div style={{ gap: 8 }}>` 不等价。
`flex: 1`、`opacity`、`lineHeight` 及合法零值属于无单位例外，不应机械补单位。

受影响的位置：

- `focus_card.tsx:186`：140/420 的容器宽高限制。
- `focus_card.tsx:201`：原图 `maxWidth: 420`、`maxHeight: 380`。
- `focus_card.tsx:211`：资料区域 `minWidth: 240`。
- `category_section.tsx:128`：网格 `gap: 8`。
- `sticker_tile.tsx`：部分圆角、字号与勾选框定位。

隔离验证使用真实本地 UI kit runtime/styles 和同构布局，不使用正式用户数据：

| 项目 | 当前数字写法 | 显式 px 对照 |
| --- | --- | --- |
| 1200 × 900 测试图实际尺寸 | 1200 × 900 | 420 × 315 |
| 图片计算样式 max-width | none | 420px |
| 图片计算样式 max-height | none | 380px |
| 图墙计算样式 gap | normal | 8px |

这证实尺寸限制和图墙间距确实失效。
未复现详情图片与下方图墙的几何重叠：该测试的 overlap 为 false。
因此“图墙盖住预览”的具体视觉现场仍应在新包实机验收中单独核查。

仅补 px 还不完整：在 360px 宽的隔离视口里，详情卡宽 279px，
图片仍为 420px，卡片 `overflow:hidden` 会裁切图片。
必须同时实施响应式容器、`minWidth: "0"`、相对宽度和 `objectFit: "contain"`。

### 4.2 详情展开不跟随浏览位置

详情固定在整面图墙顶部，点击只改状态，没有滚动定位。
用户从后面的分类点图时，当前视口仍可能显示原图墙，详情生成在上方。
这属于已确认的交互设计缺陷，不需要假设有覆盖层才能解释。

### 4.3 焦点跨区和跨搜索残留

`panel.tsx:56` 的 `focusRow` 在全库按 ID 查询，没有当前区或搜索结果约束。
注释“被筛掉自动收起”与实现不符。
切区或搜索后可能仍显示旧详情，下方却已是另一区或另一组结果。

### 4.4 原图没有用户请求优先级

`preview.ts:30` 开始的预览队列是并发 2 的 FIFO。
详情原图与图墙缩略图共享队列；`auto=true` 只直接入队，不优先执行。
已入队的过期任务不会因视图卸载而取消。
失败值缓存为空串，当前也没有显式重试入口。

这会增加等待及“点了没反应”的感受，不能据此断言本次图片完全看不到就是网络问题。

### 4.5 编辑和动作反馈不闭合

- 草稿仅初次初始化；取消后再编辑可能保留未保存草稿。
- 保存先退出表单，再等待服务端，失败时表单上下文已丢失。
- 删除失败仍可能退出详情，错误提示随之消失。
- 部分按钮没有提交中状态，可能重复触发。

后续应成功后再切状态，失败留在原操作面，取消重置草稿，并为动作加 busy 保护。

## 5. 历史弹窗陷阱仍存在

本地和 Steam 的 `.neko-page` / `.neko-card` 都带保留终帧 transform 的动画，
Card 同时 `overflow:hidden`；kit Modal 在当前位置返回 DOM，不使用 portal。
把 fixed Modal 嵌在 Card 下会被 transformed 祖先捕获，并被卡片裁切。

`DESIGN.md:284` 已记录旧版的这类问题。
当前源码已切成内联详情，不能将旧故障记录当作本次现场复现。
本轮建议继续避开在 Card 子树内使用覆盖层，不修改宿主 CSS。

## 6. 推荐优化方案

1. **图库作为默认视图。** 首屏保留总开关、当前使用区状态、区切换、搜索和常用收图入口。
   发送设置和运行诊断放到独立 tab，不再整块排在图库前。
2. **分类成为导航。** 提供分类选择与张数，桌面用紧凑侧栏，窄面板用 Select；
   进入分类后浏览图片，保留“全部分类”入口。
3. **管理动作分层。** 分类头常驻收图；改说明、删除等低频动作集中管理。
   导出、体检等库维护动作不与日常浏览争主视觉。
4. **独立详情工作区。** 点图后用详情替换当前图墙，不使用覆盖层，也不嵌套 Card。
   返回恢复当前区、分类、搜索、选择及合理的滚动位置；切区清空旧详情。
5. **图片先行。** 主图响应式完整显示，GIF 保持原图动画；梗义、分类、标签靠近图片，
   ID 和使用记录降低视觉权重。编辑保持图片可见，错误保留在当前操作面。
6. **批量选择明确。** 用单独选择模式或清晰勾选状态；批量条只在有选择时出现，
   换区清理旧选择，隐藏选择需要有明确计数和清除入口。
7. **预览队列与状态同步。** 详情原图优先、同 key 的 in-flight 请求合并、
   未启动过期任务取消、失败提供显式重试。保留缩略图懒加载和并发上限。

不要在此轮引入识图服务、语义检索、自动收集、数据迁移、新发图规则或新的宿主能力。

参考项目的独立 WebUI 使用 body 级大图层，不能原样搬进 Hosted TSX。
可借鉴的是目录导航、常用/维护动作分层及查看/选择/编辑的分离。

## 7. 验收与导入包

后续实现验收至少覆盖：

- 从首个和末尾分类点图，都能立即进入当前图片详情。
- 360 / 640 / 桌面宽度及窄 iframe：完整图片可见，无图墙盖住、无横向溢出。
- 小图、大图、超宽图、超长图、GIF、加载失败及显式重试。
- 返回浏览位置、切区、搜索、删图、禁用图后的详情状态一致。
- 编辑取消恢复原值；保存/删除失败留在当前视图；按钮不会重复提交。
- 批量选择和移分类目标正确；危险操作保留精确张数与确认。
- 总开关、积极度和提醒间隔仍写原配置，升级保留原库与用户设置。
- Hosted TSX 契约、i18n、相关逻辑测试及 `.neko-plugin` 载荷哈希校验通过。

现有工具存在宿主写入副作用：

- `tools/release_gate.py:293` 的 release 门会复制插件到宿主 `plugin/plugins` 并执行 sync。
- 同文件 `:315` 的 hosted-tsx 门会在宿主内创建和删除探针目录。
- `tools/build_package.py` 的 metadata probe 会导入宿主日志模块。
  宿主日志初始化还会扫描并清理源宿主的旧日志。

所以不能直接对原宿主执行整链，也不能只设置数据根环境变量就称为完全隔离。
建议在仓外构建目录使用独立宿主源码快照，再为子进程设置隔离的
`NEKO_STORAGE_SELECTED_ROOT` / `NEKO_STORAGE_ANCHOR_ROOT`、
`NEKO_LOG_LEVEL=INFO`、`PYTHONDONTWRITEBYTECODE=1` 和 UTF-8 输出。
不修改原宿主和 Steam 安装，最终仅交付新版本 `.neko-plugin`，由用户手动导入测试。

## 8. 隔离验证材料

- 复现脚本：`F:\ai\neko kaifa5\dist\panel-study\study-server.cjs`
- 数字尺寸截图：`F:\ai\neko kaifa5\dist\panel-study\numeric-lengths.png`
- 显式 px 对照截图：`F:\ai\neko kaifa5\dist\panel-study\explicit-pixel-lengths.png`
- 并排对照：`F:\ai\neko kaifa5\dist\panel-study\layout-comparison.png`

它们是布局诊断材料，不是优化完成后的插件面板，也不会进入发行包。
本轮完成后隔离 HTTP 服务关闭，不保留后台服务。

![隔离布局验证：左为数字长度，右为显式 px 对照](F:/ai/neko kaifa5/dist/panel-study/layout-comparison.png)

## 9. v0.20.16 实现与验收

此轮只落地图片详情相关优化，没有实施第 6 节建议的完整图库导航、设置 tab 或管理入口重排。

- 点击图库任意位置的图片，进入独立详情视图；图库和设置区暂时卸载，
  不再把详情插在图库顶端，也不采用宿主 Card 内的 fixed 弹窗。
- 返回保留浏览区、搜索、勾选和原滚动位置；返回栏在详情内 sticky，
  长资料和编辑状态也能直接返回；切区清空旧选择与详情。
- 原图使用显式 CSS 单位、响应式宽高限制和 scale-down，不放大小图；
  编辑时继续显示原图。优先调度详情原图，跳过未启动的过期缩略图任务。
- 保存成功后退出编辑；失败保留草稿与错误；取消恢复当前字段；
  删除失败保留详情；提交期间阻止重复请求。
- metadata probe 和验收工具使用隔离宿主源码快照、独立数据目录与写入/连接护栏，
  不将临时插件挂载到原宿主，不连接 Steam 正式服务。

最终隔离检查：pytest 825 项通过；前端专项回归 7 项通过；全仓 ruff 通过；
官方 SDK check 为 0 errors，隔离副本没有独立 .git 导致 1 个预期警告；
Hosted TSX 入口检查通过。导入包另用官方 PackageInspector 和独立载荷哈希审核。

浏览器使用真实本地 Hosted TSX runtime、linker、样式及官方表情包素材，但管理 API
为隔离 mock，并非 Steam 实机。验证从末尾图片进入详情后滚动为 0，图库格子数为 0；
返回还原原滚动位置和勾选；360px 窄视口、窄 sandbox iframe 及桌面视口没有横向溢出，
长表单滚动时返回栏仍可见。保存/删除失败、取消重置与切区清理均已验证。

最终截图位于 `F:\ai\neko kaifa5\dist\panel-study\detail-desktop.png`；
窄 iframe 编辑截图为同目录的 `detail-iframe-mobile-edit.png`。
截图中的模拟故障按钮仅属于验收页面，不会进入插件导入包。
Steam 的最终运行结果仍由主人手动导入新包后验收。
