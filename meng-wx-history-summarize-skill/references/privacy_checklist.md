# Privacy Checklist

Use this checklist before publishing code, sharing outputs, or handing artifacts to another person.

## Never Share

- WeChat encrypted or decrypted database files: `*.db`, `*.db-wal`, `*.db-shm`.
- Key material: passphrases, derived database keys, `wechat_db_keys.json`, `all_keys.json`.
- Raw exports: JSONL, CSV, TXT, unredacted metadata JSON, or full chat transcripts.
- Local identity paths containing real Windows usernames, real `wxid_*`, or real `*@chatroom` IDs.
- API keys, `.env`, logs, screenshots with private chat content.

## Safe To Share After Review

- Reusable scripts with placeholders only.
- Skill instructions and beginner documentation.
- Final summary PDFs or Markdown only after manual review and redaction.

## Local Search Before Commit

Run from the repository root:

```powershell
rg -n "wxid_|@chatroom|wechat_db_keys|all_keys|passphrase|enc_key|C:\\Users|[0-9a-fA-F]{64}"
git status --short
```

Expected staged files should be limited to the skill folder, root metadata, and dependency files.
