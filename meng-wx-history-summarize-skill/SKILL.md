---
name: meng-wx-history-summarize-skill
description: "用于在用户明确拥有数据访问权时，本地处理微信 PC 4.x 聊天记录：恢复和解密数据库、扫描群聊、导出指定群、生成 Markdown 日报、详细 PDF 日报或 PDF 月报。适用于用户要求总结自己的微信群聊、查找群 ID、批量生成日报或月报，并要求避免泄露密钥、数据库、原始聊天记录、个人路径和群 ID 的场景。"
---

# 微信聊天记录总结 Skill

## 基本原则

只处理用户本人有权访问的数据。不要打印、提交、上传或分享 passphrase、数据库派生密钥、`.private/`、解密数据库、JSONL/CSV/TXT 原始导出、完整聊天正文和未脱敏报告。

所有处理默认在本地完成。共享结果前，先做脱敏检查。

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

   解密结果默认写入 `outputs/decrypted_databases`，密钥映射默认写入 `.private/wechat_db_keys.json`。`.private/` 不得分享或提交。

3. **扫描群聊**

   ```powershell
   python scripts/scan_group_chats.py --db-root outputs/decrypted_databases
   ```

   从 `outputs/group_scan/微信群聊分类清单.csv` 查找目标群的 `chat_id`。

4. **导出指定群**

   必须显式传入 `--chat`，不要在脚本或文档里硬编码真实群 ID。

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

## 提交或分享前检查

运行：

```powershell
rg -n "wxid_|@chatroom|wechat_db_keys|all_keys|passphrase|enc_key|C:\\Users|[0-9a-fA-F]{64}"
git status --short
```

确认没有提交：

- `.private/`
- `outputs/`
- `*.db`、`*.db-wal`、`*.db-shm`
- `*.jsonl`、`*.csv`、`*.txt`
- 未脱敏 `*.pdf`
- 真实本机路径、真实 `wxid_*`、真实 `*@chatroom`

## 资源

- `references/beginner_usage.md`：给同事的小白中文使用说明。
- `references/privacy_checklist.md`：发布和分享前的隐私检查清单。
- `scripts/recover_wechat4.py`：恢复并解密微信 4.x 数据库。
- `scripts/scan_group_chats.py`：扫描和分类群聊。
- `scripts/export_target_chat.py`：导出单个群聊。
- `scripts/summarize_daily_chat.py`：生成 Markdown 日报。
- `scripts/generate_detailed_report.py`：生成详细 PDF 日报。
- `scripts/generate_monthly_report.py`：生成 PDF 月报。
