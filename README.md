# 微信聊天记录总结 Skill

这个仓库包含 `meng-wx-history-summarize-skill`，用于在本地处理用户本人有权访问的微信 PC 4.x 聊天记录：数据库恢复、群聊扫描、指定群导出、日报和月报生成。

仓库不包含微信数据库、密钥、原始聊天导出、个人路径、真实群 ID 或未脱敏报告。

## 入口

- Skill 说明：`meng-wx-history-summarize-skill/SKILL.md`
- 小白使用文档：`meng-wx-history-summarize-skill/references/beginner_usage.md`
- 隐私检查清单：`meng-wx-history-summarize-skill/references/privacy_checklist.md`

## 安全边界

不要提交或分享：

- `.private/`
- `outputs/`
- `*.db`、`*.db-wal`、`*.db-shm`
- `*.jsonl`、`*.csv`、`*.txt`
- 未脱敏 `*.pdf`
- passphrase、数据库密钥、真实 `wxid_*`、真实 `*@chatroom`
