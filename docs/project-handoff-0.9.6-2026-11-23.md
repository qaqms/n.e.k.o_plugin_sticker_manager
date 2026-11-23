# 0.9.6 续接开发文档：免配模型、降级与概率发图

更新日期：2026-11-23
前一份交接：[project-handoff-2026-11-23.md](project-handoff-2026-11-23.md)（目标与宿主背景仍有效，其中“当前代码状态与已知缺口”一节已被本文取代）

## 一句话现状

分支 `feature/reply-hook-0.9.6` 已推送，插件版本 `0.9.6`。新安装无需配置插件模型（宿主 #3741 自动绑定到使魔模型）；选图模型不可用时自动降级回主模型工具；发图频率改为**每条回复按概率掷一次**，默认 50%（约两轮一张）、冷却 0 秒，三个积极度档位已从面板移除。单测、ruff 与最新宿主的 check / release / hosted-tsx 门禁全部通过，**但真实宿主端到端（免费模型实际选图并显示）尚未验证**。

## 本分支提交

| 提交 | 内容 |
| --- | --- |
| `0912e93` | 0.9.6：宿主网关错误码分别诊断；持续失败降级回工具模式；文档改为“无需绑定” |
| `971be5e` | 面板状态类型补 `reply_completed.fallback_reason`（修 hosted-tsx 门禁） |
| `020a7f6` | 概率发图替代档位；一次性迁移已有用户设置；面板改为概率滑块 |

导入包：`F:\ai\neko kaifa4\dist\sticker_manager_v0.9.6.neko-plugin`（用最新宿主构建，sha256 前缀 `5330104a2ae39231`）。分支尚未合并到主线、未打 tag、未提 PR。

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

- 当前 1411 passed、2 skipped，约 65 秒。`test_reply_tail_transport.py` 在本机环境下损坏，需排除。
- `tests/conftest.py` 有 autouse 夹具 `probability_always_hits`：把 `sender` 与 `reply_completed` 的 `_RNG` 换成恒命中，避免默认 0.5 让无关测试随机失败。测概率闸的测试自行替换 `_RNG`。
- 依赖旧默认冷却 20 秒的测试已改为显式设置 `cooldown_sec=20.0`。
- 新增：`tests/test_send_probability.py`（迁移与入口）；`tests/test_reply_completed.py` 末尾的概率用例（未命中不调模型、命中后发送器不二次掷、every_reply 不掷、概率 0 永不选）。
- 没有 `pytest-timeout`；若怀疑卡死，先确认没有残留的 pytest 进程（之前就是两份重复运行导致误判）。

门禁（在插件目录执行）：

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

1. **端到端验证**（最重要，尚未做）：用新包导入测试宿主，确认首启迁移生效（面板概率 50%、冷却 0），打开发送总开关后连续聊 10 轮左右，分别确认四个阶段——调用到宿主模型、模型选出图、图登记到原回复、前端实际显示；顺带记录 `probability_skip` 与实际发图比例。
2. 面板“测试选图”从入口点触发时日志里未看到记录，原因未查明；需确认它确实调用了免费模型并返回结果。
3. 覆盖降级路径的实机验证：免费额度用完（`model_quota_exceeded`）、使魔模型未配置时，工具模式是否正确恢复、恢复后是否不重复发图。
4. `plugin.toml` 补 `[permissions]` 表：check 目前对 `system.env:read`、`system.network:connect` 给 warning，宿主后续版本会升级为 error；同时评估 `models:invoke`、`main_agent.*`、`system.fs:*` 是否要声明。
5. 档位清理：确认没有用户依赖后，删掉 `eagerness`、`every_reply`、`eager_attachment_turns` 及 `core/eagerness.py` 相关提示路径和测试（旧工具模式仍在用档位文案，删除前需先决定工具模式的提示词）。
6. 新手引导：宿主使魔模型不可用时，引导用户去宿主 API 设置，而不是插件模型配置。
7. 发布：合并分支、打 tag、走 Market CI（本地门禁不代表远端 CI 通过）。

## 主要代码位置

- 概率闸、降级、选图调用：`services/reply_completed.py`（`handle`、`maintain`、`_fall_back`、`_recovered`、`test_selection`）
- 错误码映射：`core/attachment_selection.py`（`model_error_status`、`PERSISTENT_MODEL_FAILURES`）
- 发送器概率闸与冷却：`services/sender.py`
- 迁移与入口：`__init__.py`（`_migrate_send_settings`、`set_send_probability_entry`、`_refresh_send_tool_surface`）
- 默认值：`core/configuration.py` 的 `SendSettings`、`plugin.toml`、`config.example.toml`
- 面板：`ui/components/awareness_card.tsx`、`ui/shared.ts`；文案 `i18n/zh-CN.json`、`i18n/en.json`
