# Changelog

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
