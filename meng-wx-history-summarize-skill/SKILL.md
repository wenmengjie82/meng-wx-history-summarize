---
name: meng-wx-history-summarize-skill
description: Recover, decrypt, export, scan, and summarize authorized WeChat PC 4.x chat history into Markdown or PDF reports. Use when a user asks to process their own WeChat history, identify group chats, export one chatroom, generate daily summaries, generate detailed daily PDFs, or generate monthly PDF reports while avoiding secrets, database keys, raw chat logs, and personal data in shared outputs.
---

# Meng WeChat History Summarize Skill

## Core Rule

Only process data the user is authorized to access. Never print, commit, upload, or summarize raw secrets such as passphrases, derived database keys, `wechat_db_keys.json`, decrypted databases, JSONL/CSV/TXT chat exports, or full chat records.

## Workflow

1. **Set up dependencies**
   - Use Python 3.11+.
   - Install repository requirements: `pip install -r requirements.txt`.
   - On Windows, run commands from the skill folder or pass absolute paths.

2. **Recover and decrypt WeChat 4.x databases**
   - Require the encrypted `db_storage` directory via `--db-dir` or `WECHAT_DB_STORAGE_DIR`.
   - Use `scripts/recover_wechat4.py`.
   - Keep key output under `.private/`; never expose it.

   ```powershell
   python scripts/recover_wechat4.py --db-dir "<path-to-db_storage>"
   ```

3. **Scan group chats before exporting content**
   - Use `scripts/scan_group_chats.py` on decrypted databases.
   - Review `outputs/group_scan/微信群聊分类清单.csv` to find the target `chat_id`.

   ```powershell
   python scripts/scan_group_chats.py --db-root outputs/decrypted_databases
   ```

4. **Export a single authorized chatroom**
   - Use `scripts/export_target_chat.py`.
   - `--chat` is required; do not rely on hardcoded IDs.

   ```powershell
   python scripts/export_target_chat.py --db-root outputs/decrypted_databases --chat "<chatroom_id>"
   ```

5. **Generate summaries**
   - Simple daily Markdown: `scripts/summarize_daily_chat.py`.
   - Detailed daily PDF: `scripts/generate_detailed_report.py`.
   - Monthly PDF: `scripts/generate_monthly_report.py`.
   - AI summaries use OpenAI-compatible environment variables such as `DEEPSEEK_API_KEY` or `ANTHROPIC_AUTH_TOKEN`. If AI is unavailable, prefer scripts with local fallback (`summarize_daily_chat.py`, `generate_monthly_report.py --no-ai`) instead of failing the whole task.

## Safety Checklist

Before sharing or committing:

- Run a search for local usernames, `wxid_`, `@chatroom`, 64-hex secrets, `.db`, `.jsonl`, `.csv`, `.txt`, `.pdf`, and `wechat_db_keys`.
- Confirm only scripts, skill instructions, references, and harmless metadata are staged.
- Do not stage `outputs/`, `.private/`, decrypted databases, exported chat records, reports generated from private chats, API keys, or logs.

## Resources

- `references/beginner_usage.md`: Chinese, beginner-friendly operating guide for colleagues.
- `references/privacy_checklist.md`: Security review checklist before publishing or sharing outputs.
- `scripts/recover_wechat4.py`: Recover cached passphrase candidates and decrypt WeChat 4.x databases.
- `scripts/scan_group_chats.py`: Scan and classify group chats from decrypted metadata.
- `scripts/export_target_chat.py`: Export one chatroom to JSONL/CSV/TXT.
- `scripts/summarize_daily_chat.py`: Generate daily Markdown summaries.
- `scripts/generate_detailed_report.py`: Generate detailed daily PDFs.
- `scripts/generate_monthly_report.py`: Generate monthly PDFs with chunked AI summarization and local fallback.
