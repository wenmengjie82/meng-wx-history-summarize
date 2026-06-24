---
name: meng-wx-history-summarize-skill
description: "用于在用户明确拥有数据访问权时，本地处理微信 PC 4.x 聊天记录：恢复和解密数据库、扫描群聊、导出指定群、生成 Markdown 日报、详细 PDF 日报或 PDF 月报。适用于用户要求总结自己的微信群聊、查找群 ID、批量生成日报或月报，并要求在本地处理、避免泄露敏感信息的场景。"
---

# 微信聊天记录总结 Skill

## 基本原则

只处理用户本人有权访问的数据。所有处理默认在本地完成；共享结果前由使用者自行确认合规和脱敏状态。

## 标准流程

1. **安装依赖**

   ```powershell
   pip install -r requirements.txt
   ```

2. **解密微信数据库**

   要求用户提供本机微信 `db_storage` 目录，或设置 `WECHAT_DB_STORAGE_DIR`。

   ```powershell
   python scripts/recover_wechat4.py --db-dir "<path-to-db_storage>"
   ```

   解密结果默认写入 `outputs/decrypted_databases`，密钥映射默认写入 `.private/wechat_db_keys.json`。

3. **扫描群聊**

   ```powershell
   python scripts/scan_group_chats.py --db-root outputs/decrypted_databases
   ```

   从 `outputs/group_scan/微信群聊分类清单.csv` 查找目标群的 `chat_id`。

4. **导出指定群**

   必须显式传入 `--chat`。

   ```powershell
   python scripts/export_target_chat.py --db-root outputs/decrypted_databases --chat "<chatroom_id>"
   ```

5. **生成报告**

   日报 Markdown：

   ```powershell
   python scripts/summarize_daily_chat.py --input "outputs/chat_exports/<群名>.jsonl" --date YYYY-MM-DD
   ```

   详细日报 PDF：

   ```powershell
   python scripts/generate_detailed_report.py --input "outputs/chat_exports/<群名>.jsonl" --date YYYY-MM-DD --group "<群名>"
   ```

   月报 PDF：

   ```powershell
   python scripts/generate_monthly_report.py --input "outputs/chat_exports/<群名>.jsonl" --month YYYY-MM --group "<群名>"
   ```

   没有 AI 接口时，月报可加 `--no-ai` 生成本地统计版。AI 接口使用 OpenAI-compatible 环境变量，例如 `DEEPSEEK_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`DEEPSEEK_BASE_URL` 或 `ANTHROPIC_BASE_URL`。

## 失败处理

连续失败时先检查假设，不要盲试密钥：

- 当前值是 passphrase 还是最终数据库 key。
- 是否按每个数据库第一页 salt 派生 key。
- 是否使用第一页 HMAC 做确定性验证。
- 是否指向正确的 `db_storage` 目录。
- 是否处理的是同一台机器、同一微信账号的数据。

## 资源

- `references/beginner_usage.md`：给同事的小白中文使用说明。
- `scripts/recover_wechat4.py`：恢复并解密微信 4.x 数据库。
- `scripts/scan_group_chats.py`：扫描和分类群聊。
- `scripts/export_target_chat.py`：导出单个群聊。
- `scripts/summarize_daily_chat.py`：生成 Markdown 日报。
- `scripts/generate_detailed_report.py`：生成详细 PDF 日报。
- `scripts/generate_monthly_report.py`：生成 PDF 月报。
