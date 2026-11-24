# 0.9.6 续接开发文档：免配模型、降级与概率发图

更新日期：2026-11-24
前一份交接：[project-handoff-2026-11-23.md](project-handoff-2026-11-23.md)（目标与宿主背景仍有效，其中“当前代码状态与已知缺口”一节已被本文取代）

## 一句话现状

`0.9.6` 的代码已合并到 `main`，**尚未发行**：维护者决定继续开发，之后改版本号一起发。仓库里有一个 `v0.9.6` 标签（指向 `21b0d6e`），当时 Release 工作流因缺权限没能启动，没有发布页和安装包；工作流已在 `a87fce7` 修好。发行工作流要求标签与 `plugin.toml` 版本一致，所以**下次发行从 `0.9.7` 起**。插件市场从未通知（Steam 版宿主尚未更新到 #3585 / #3741 所需版本）。新安装无需配置插件模型（宿主 #3741 自动绑定到使魔模型）；选图模型不可用时自动降级回主模型工具；发图频率改为**每条回复按概率掷一次**，默认 50%（约两轮一张）、冷却 0 秒，三个积极度档位已从面板移除；选图请求用答案预填，免费模型 1–3 秒出结果。维护者已在真实宿主中实测，效果良好。

## 提交记录（均已在 main）

| 提交 | 内容 |
| --- | --- |
| `0912e93` | 0.9.6：宿主网关错误码分别诊断；持续失败降级回工具模式；文档改为“无需绑定” |
| `971be5e` | 面板状态类型补 `reply_completed.fallback_reason`（修 hosted-tsx 门禁） |
| `020a7f6` | 概率发图替代档位；一次性迁移已有用户设置；面板改为概率滑块 |
| `175262d` | 本交接文档初版 |
| `b11e6d4` | 选图请求预填答案开头 `{"choice":`，免费推理模型提速 |
| `9ee0bf0` | `[permissions]` 权限声明；修复市场 Verify 的 Ruff 导入排序；发行文档 |
| 本轮 | 修复市场 Verify 的测试运行：去掉测试间相对导入；假时钟只作用于插件模块 |

目前没有 0.9.6 的正式安装包；本地 `dist/` 下的包只供测试。发行流程与“不通知市场”的约定见 [updates-and-release.md](updates-and-release.md)，发行说明见 [releases/0.9.7.md](releases/0.9.7.md)。

## 产品决策（来自维护者，勿擅改）

1. **开箱即用**：不要求用户给插件单独配模型；需要少量配置时以后做新手引导。
2. **频率用概率控制**：参考同类项目 `参考项目/astrbot_plugin_meme_manager` 的“每条回复按概率触发 + 辅助模型只判断贴不贴切”。默认 0.5、冷却 0。
3. **档位逐步移除**：矜持/自然/爱发不再出现在面板，代码与配置字段暂时保留（`set_eagerness`、`set_every_reply`、`set_eager_attachment_turns` 入口仍在，只是不显示）。
4. **已有用户直接被新配置覆盖**：升级后首启统一改写发送设置，只做一次。
5. 测试使用**宿主的免费模型**（每日 500 次，与使魔任务共用额度）。

## 机制说明

### 模型调用与自动绑定

- 调用：`ctx.models.get_client()` → `chat.completions.create(model="eager_attachment")`。
- 宿主 #3741 在安装/更新提交时（`notify_install_committed`）把未绑定的用途绑到 `host_follow` 的 `agent`（使魔模型）槽位；已有绑定和主动解绑都保留。
- 网关错误体 `{"error": {"code": ...}}`，`core/attachment_selection.py` 的 `model_error_status` 映射：

| 宿主错误码 | 插件状态 | 是否持续性 |
| --- | --- | --- |
| `model_usage_not_bound` | `model_unbound` | 是 |
| `host_api_not_configured` | `host_model_unconfigured` | 是 |
| `host_api_unsupported` | `host_model_unsupported` | 是 |
| `agent_quota_exceeded` | `model_quota_exceeded` | 是 |
| `model_defaults_incompatible` | `model_defaults_incompatible` | 是 |
| 其它异常 | `model_unavailable` | 否（计入连续失败） |

### 降级（`services/reply_completed.py`）

- 模式：`pending` / `hook` / `fallback` / `legacy`；`managed = mode in {"pending", "hook"}` 时撤下 `sticker_list` / `sticker_send`。
- 触发：一次持续性错误，或连续 3 次（`TRANSIENT_FAILURE_LIMIT`）一般失败；模型有任何应答即清零。**超时不触发降级。**
- 顺序：**先退订回复完成通知，确认成功后才恢复工具**；退订结果不确定时保持工具撤下，避免同一轮 hook 与工具重复发图。
- 恢复：每 600 秒（`FALLBACK_RETRY_SEC`）重新订阅；面板“测试选图”通过后立即恢复。
- 面板通过 `snapshot()["fallback_reason"]` 展示降级原因。

### 概率发图

- `handle()` 在冷却、`send_busy` 检查之后、调用模型**之前**掷骰：`_RNG.random() >= probability` 则记 `probability_skip` 返回，不耗模型额度。
- `every_reply=True` 或 `probability >= 1.0` 时不掷。
- 命中后交给发送器时把 `probability` 置 1.0，发送器不再二次掷骰（避免实际概率变成 p²）。
- 选图提示词只剩一句 `_SELECTION_GUIDANCE`：有贴切的就选一张，不贴切就跳过。
- 面板入口 `set_send_probability`（0–1，校验 bool/NaN/越界），写 `sticker_manager.send.probability` 后重载并回读确认。

### 答案预填（`b11e6d4`）

- 选图请求在消息末尾多放一条助手消息 `{"choice":`（`ANSWER_PREFIX`）。推理模型的思考发生在回复开头，开头被占后直接续写序号，免费模型实测 1.2–2.6 秒、思考 0 token。
- 返回内容不以 `{` 或 ``` 开头时视为续写，补上前缀再交给 `parse_reply_selection`；返回完整 JSON 的供应商照常解析。`choice` 只会是整数或关键词，不会是对象，所以这条判断不会误伤。
- 首次请求报 `upstream_request_rejected` / `invalid_request` / `unsupported_parameter` 时，改发普通请求，并把 `_prefill` 置 False 直到插件重启。宿主把几乎所有 4xx 都归为 `upstream_request_rejected`，因此无关的 4xx 也会关掉预填并多耗一次额度；重发与首次共用 12 秒上限。
- 面板与日志的 `prefill` 字段：`used` / `off` / `rejected`。
- 已评估并放弃“主回复开始时就并行选图”：选图必须同时看到用户的话和猫娘的回复，而回复只有说完才拿得到；宿主也只允许在 `after_reply` 回调期间登记图片。

### 权限声明（`plugin.toml` 的 `[permissions]`）

宿主 #3940 起，`check -r` 会把代码扫描结果与声明比对；明确用到却未声明会报错，完全没有权限表目前只是警告，以后也会变成错误。当前声明：

| 权限 | 类型 | 用途 |
| --- | --- | --- |
| `plugin_sdk.main_agent.messages:push` | require | 发送表情包 |
| `system.network:connect`（`127.0.0.1`） | require | 本机宿主接口查角色与状态 |
| `system.env:read` | require | 定位宿主数据目录（规范要求环境变量一律 require） |
| `plugin_sdk.models:invoke` | ask，background | 回复完成回调里选图 |
| `plugin_sdk.main_agent.conversations:read` / `memory:read` | ask，background | 旧宿主补图轮询读最近一轮 |
| `system.fs:read` | ask，background | 旧宿主读日志判断回复结束 |

不声明 `system.fs:write`：插件只写自己的数据目录，扫描对此给出“不确定”警告属正常。新增代码若用到上表以外的能力，需同步补声明，否则市场 CI 会失败。

### 一次性设置迁移（`__init__.py`）

- `on_startup` 在 `_reload_settings()` 前调用 `_migrate_send_settings()`。
- 标记文件在插件数据目录：`settings_version.json`，`{"version": 2}`。**故意不放在配置默认值里**，否则 manifest 默认值会掩盖旧的覆盖值。
- 版本低于 2 时写入 `_SETTINGS_V2`：`probability=0.5`、`cooldown_sec=0.0`、`every_reply=False`、`eagerness="natural"`、`eager_attachment_turns=1`；写入失败不落标记，下次启动重试；标记损坏视为未迁移。
- 新安装也会走一次（写入的就是默认值，无副作用）。
- 以后再改默认值需要迁移时：`_SETTINGS_VERSION` +1，并只写需要变的键。

### 配置三处同步

`plugin.toml` 的 `[sticker_manager]`、`config.example.toml`、`core/configuration.py` 的 dataclass 默认值必须一致，由 `tests/test_config_docs_sync.py` 强制。

## 测试

```powershell
& "F:\ai\neko kaifa7\最新版本的宿主\N.E.K.O\.venv\Scripts\python.exe" -m pytest tests -q -p no:cacheprovider --deselect tests/test_reply_tail_transport.py
```

- 当前 1417 passed、2 skipped，约 70 秒。`test_reply_tail_transport.py` 在本机环境下损坏，需排除。
- `tests/conftest.py` 有 autouse 夹具 `probability_always_hits`：把 `sender` 与 `reply_completed` 的 `_RNG` 换成恒命中，避免默认 0.5 让无关测试随机失败。测概率闸的测试自行替换 `_RNG`。
- 依赖旧默认冷却 20 秒的测试已改为显式设置 `cooldown_sec=20.0`。
- 新增：`tests/test_send_probability.py`（迁移与入口）；`tests/test_reply_completed.py` 末尾的概率用例（未命中不调模型、命中后发送器不二次掷、every_reply 不掷、概率 0 永不选）。
- 测试共用的 `ctx.reply_tail` 桩 `TailClient` 在 `tests/conftest.py`。**不要在测试里 `from tests.xxx import`**：市场 CI 在上一级目录运行 Ruff，`tests` 会被当作第三方包，导入分组与本地不同，Verify 会因 I001 失败（0.9.6 合并前的 6 次 Verify 均因此失败）。
- 市场 CI 的 `check -r` 会在挂载到宿主 `plugin/plugins/sticker_manager` 的副本上，用**宿主的** `pytest.ini`、以宿主为 rootdir 跑本插件测试（带 pytest-randomly 固定种子与宿主的 clock_guard）。由此两条规矩：
  - 测试之间共用辅助函数写 `from test_xxx import ...`，不要写相对导入 `from .test_xxx`（`tests/` 不是包）；`conftest.py` 会把 `tests/` 放进 `sys.path` 供本仓的 importlib 模式使用。
  - 假时钟用 `conftest.patch_plugin_clock(monkeypatch, monotonic=..., time=...)`，不要 `monkeypatch.setattr(xxx.time, "monotonic", ...)`——后者替换的是全局 `time`，会被 clock_guard 判失败。
- 本地复现市场 CI：把插件按 `git ls-files` 复制到一份宿主 `main` 的 `plugin/plugins/sticker_manager`，在宿主根目录执行 `PYTHONUTF8=1 python -m plugin.neko_plugin_cli.cli check -r sticker_manager`（Windows 不设 `PYTHONUTF8` 会因 GBK 解码失败）。本仓的 `tools/release_gate.py` 会跳过宿主侧测试，查不出这类问题。
- 没有 `pytest-timeout`；若怀疑卡死，先确认没有残留的 pytest 进程（之前就是两份重复运行导致误判）。

门禁（在插件目录执行；`ruff` 关卡与市场 CI 同参数，但目录布局不同，见上条）：

```powershell
& "F:\ai\neko kaifa7\最新版本的宿主\N.E.K.O\.venv\Scripts\python.exe" tools/release_gate.py --host-root "F:/ai/neko kaifa7/最新版本的宿主/N.E.K.O" --only check,release,hosted-tsx
```

构建导入包：

```powershell
$env:PYTHONIOENCODING = 'utf-8'; & "F:\ai\neko kaifa7\最新版本的宿主\N.E.K.O\.venv\Scripts\python.exe" tools/build_package.py --host-root "F:/ai/neko kaifa7/最新版本的宿主/N.E.K.O"
```

## 测试宿主

- 源码：`F:\ai\neko kaifa7\最新版本的宿主\N.E.K.O`（含 #3585、#3741）。
- 启动：`F:\ai\neko kaifa7\最新版本的宿主\start-test-host.ps1`（`-Fresh` 清空测试数据）。数据隔离在同级 `test-profile\`，实际落在 `test-profile\LocalAppData\N.E.K.O`，不碰真实用户数据。与正式版同用 48911 端口，不能同时开。
- 端口：主 48911、插件服务 48916、agent 48915、memory 48912。
- 新 profile 没有 `core_config`，默认 Qwen 会报 401“API 无效”：需在 `/api_key` 把语音与通用供应商都设为“免费版”并保存。已确认之后槽位为 `free-agent-model`，`eager_attachment` 自动绑定到“Familiar model”，hook 已订阅。

### Windows 环境坑

- Python 3.11 按 GBK 读 `.pth`，中文路径下 editable 安装会崩：宿主目录**只能用** `uv sync --no-install-project`，不要直接 `uv run` / `uv sync`。
- npm 全局配置里的代理 `127.0.0.1:7897` 已失效；构建前端时在命令行传 `--proxy` / `--https-proxy`（环境变量里的 `192.168.124.10:7891` 可用），不要改 npm 配置。
- `build_frontend.bat` 需用完整路径调用。
- 插件目录里有一个误建的空 `.venv`，需手动删除。

## 待办（按优先级）

1. **通知插件市场**：等 Steam 版宿主更新到包含 #3585、#3741 的版本后，按 [updates-and-release.md](updates-and-release.md) 的“市场发布准备”提交届时的新版本（不早于 `0.9.7`）。在此之前不要调用 `neko-plugin publish`。
2. 预填的小改进（可选）：只对宿主真正表示“不支持该参数/消息形状”的错误关闭预填；或在换绑模型后重新尝试预填。
3. 档位清理：确认没有用户依赖后，删掉 `eagerness`、`every_reply`、`eager_attachment_turns` 及 `core/eagerness.py` 相关提示路径和测试（旧工具模式仍在用档位文案，删除前需先决定工具模式的提示词）。
4. 新手引导：宿主使魔模型不可用时，引导用户去宿主 API 设置，而不是插件模型配置。
5. 补充降级路径的实机记录：免费额度用完（`model_quota_exceeded`）、使魔模型未配置时，工具模式是否正确恢复、恢复后是否不重复发图。

已完成：真实宿主端到端验证（维护者实测效果良好）、`[permissions]` 权限表、市场 Verify 修复、Release 工作流权限修复。

下次发行步骤：`plugin.toml`、`pyproject.toml`、`uv.lock` 改版本号；CHANGELOG 新增版本节；写好 `docs/releases/` 下的发行说明（0.9.7 这三步已完成，见 `docs/releases/0.9.7.md`）；等 Verify 通过后推 `vX.Y.Z` 标签；Release 成功后用 `gh release edit --notes-file` 填说明并核对三个资产。是否删除悬空的 `v0.9.6` 标签由维护者决定。

## 主要代码位置

- 概率闸、降级、选图调用：`services/reply_completed.py`（`handle`、`maintain`、`_fall_back`、`_recovered`、`test_selection`）
- 错误码映射：`core/attachment_selection.py`（`model_error_status`、`PERSISTENT_MODEL_FAILURES`）
- 发送器概率闸与冷却：`services/sender.py`
- 迁移与入口：`__init__.py`（`_migrate_send_settings`、`set_send_probability_entry`、`_refresh_send_tool_surface`）
- 默认值：`core/configuration.py` 的 `SendSettings`、`plugin.toml`、`config.example.toml`
- 权限声明：`plugin.toml` 的 `[permissions.require]` / `[permissions.ask]`
- 发行：`docs/updates-and-release.md`、`docs/releases/0.9.7.md`、`.github/workflows/`
- 面板：`ui/components/awareness_card.tsx`、`ui/shared.ts`；文案 `i18n/zh-CN.json`、`i18n/en.json`
