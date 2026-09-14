# Changelog

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
