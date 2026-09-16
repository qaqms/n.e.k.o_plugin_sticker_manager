# 表情包管理 (sticker_manager)

给她一个自己的表情包收藏间。

主人在面板里收藏表情包（上传、写描述、打标签、禁用、删除），猫娘通过
`sticker_list` / `sticker_send` 两个工具在对话里自主挑一张发出去。
每次发送（含失败的尝试）都记进使用台账，面板能看到她最近爱用什么。

## 能力面

| 面 | 内容 |
| --- | --- |
| 管理入口 | `add` / `update` / `remove` / `send` / `list` / `preview` / `history` / `switch` / `repair` / `import_inbox` / 直传三入口 `import_upload_start`→`chunk`→`finish` / `group_set_desc` / `batch_update` / `batch_remove` |
| 轻打标（轮 F） | 逐图文本全可选：一句**分组说明**就是她看到的分类目录正文；目录行正文回落 `梗义>描述>分组说明>未标注`，不造假描述；批量勾选即可加/删标签、移组、启停、删除（删除前摊精确张数） |
| LLM 工具 | `sticker_list`（看目录）、`sticker_send`（按 id 或关键词发），带**注册心跳**：宿主/main_server 重启后静默缺席的工具每 5 分钟被点名补挂 |
| 面板 | hosted-tsx：收藏表单、缩略图网格（懒加载）、编辑弹窗、使用台账、总开关 |
| 格式 | png / jpg / gif / webp（只认文件头），单张 ≤8MiB；gif 动图保动画直发 |
| 批量导入 | **面板「选择套图包导入」选 `.zip` 直传**（v0.6.0，分块上传、manifest 随行带描述/标签/套图/梗义）；图片多选一次收一批（文件名当描述，单轮≤64 张）；高级旁路：把图/包放进 `data/library/inbox/` 点「导入收件箱」服务端整批收 |
| 防重复 | 入库记内容指纹（sha256），同图再传如实拒绝；旧图 lazy 回填 |
| 库体检 | `repair` 入口/面板按钮：清掉丢图的条目与孤儿文件，补旧条目指纹 |

## 开关语义

`[sticker_manager].enabled = false`（默认，fail-closed）：她不发图、看不到目录，
但库的收藏/编辑/删除照常可用。总开关在面板顶部或 `switch` 入口切换。

## 频控与容量

- 同一角色卡两次发送之间默认冷却 20 秒（`[sticker_manager.send].cooldown_sec`）；
- **两级目录选图（轮 F）**：`sticker_list` 无词时先报【套图分类】再报条目，带 `group` 只看那一组；
  `sticker_send(group=)` 只拍板“这组调性对”，组内先过近期去重尺再随机选一张（id > group > query）；
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
