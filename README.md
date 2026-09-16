# 表情包管理 (sticker_manager)

给她一个自己的表情包收藏间。

主人在面板里收藏表情包（上传、写描述、打标签、禁用、删除），猫娘通过
`sticker_list` / `sticker_send` 两个工具在对话里自主挑一张发出去。
每次发送（含失败的尝试）都记进使用台账，面板能看到她最近爱用什么。

## 能力面

| 面 | 内容 |
| --- | --- |
| 管理入口 | `add` / `update` / `remove` / `send` / `list` / `preview` / `history` / `switch` / `repair` / `import_inbox` |
| LLM 工具 | `sticker_list`（看目录）、`sticker_send`（按 id 或关键词发），带**注册心跳**：宿主/main_server 重启后静默缺席的工具每 5 分钟被点名补挂 |
| 面板 | hosted-tsx：收藏表单、缩略图网格（懒加载）、编辑弹窗、使用台账、总开关 |
| 格式 | png / jpg / gif / webp（只认文件头），单张 ≤8MiB；gif 动图保动画直发 |
| 批量导入 | 面板多选一次收一批（文件名当描述，单轮≤64 张）；或把图丢进 `data/library/inbox/` 点「导入收件箱」服务端整批收 |
| 防重复 | 入库记内容指纹（sha256），同图再传如实拒绝；旧图 lazy 回填 |
| 库体检 | `repair` 入口/面板按钮：清掉丢图的条目与孤儿文件，补旧条目指纹 |

## 开关语义

`[sticker_manager].enabled = false`（默认，fail-closed）：她不发图、看不到目录，
但库的收藏/编辑/删除照常可用。总开关在面板顶部或 `switch` 入口切换。

## 频控与容量

- 同一角色卡两次发送之间默认冷却 20 秒（`[sticker_manager.send].cooldown_sec`）；
- **跨轮去重**：她自主选图时，同一角色卡最近 5 张成功发过的不再出现
  （`recent_dedup_count`，0=关；台账源，重启不失忆）；主人点名要再看某张时
  她可用 `sticker_send(force=true)` 绕行（冷却仍生效）；
- **概率闸门**：`send.probability` 控制她发图意愿到实际放行的比例（默认 1.0=关）。
  同一角色卡一个复用窗口（`probability_reuse_sec`）内只掷一次判定，不叠加惩罚；
- ≤256KiB 的图走内联通道（保留原字节与 gif 动画），更大的静态图走宿主上传换 URL；
  动图超过内联预算会**如实拒绝**而不是被压平成 JPEG；
- 给模型看的目录默认最多 80 条（按最近使用排序），台账保留 200 条。

## 开发闭环

```bash
uv run python tools/release_gate.py   # 五门：pytest / ruff / check / release / hosted-tsx
```

详见 `DESIGN.md`。
