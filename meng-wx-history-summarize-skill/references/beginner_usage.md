# 小白使用说明：微信聊天记录自动总结

这份文档写给第一次使用的同事。目标是：在不上传微信数据库、不泄露密钥、不公开聊天记录的前提下，把自己有权访问的微信群聊天记录导出并生成日报或月报。

## 先看安全边界

可以做：

- 处理你本人有权访问的微信数据。
- 在自己电脑本地解密、导出、总结。
- 只分享最终确认可以公开的总结文件。

不要做：

- 不要把 `.private/`、`outputs/`、数据库文件、聊天导出文件发给别人。
- 不要把 `wechat_db_keys.json`、`all_keys.json`、passphrase、数据库密钥发给任何人。
- 不要把没有脱敏的 PDF、JSON、CSV、TXT 推到 GitHub。
- 不要处理别人未授权的数据。

## 你需要准备什么

1. Windows 电脑。
2. 标准微信 PC 4.x 已经登录过，并且本机有微信数据库。
3. Python 3.11 或更高版本。
4. 如果要 AI 深度总结，需要配置一个 OpenAI-compatible 接口密钥，例如 `DEEPSEEK_API_KEY` 或 `ANTHROPIC_AUTH_TOKEN`。没有密钥也可以做本地统计版。

## 第一步：安装依赖

打开 PowerShell，进入仓库目录：

```powershell
cd <仓库目录>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

如果 PowerShell 不允许激活虚拟环境，先运行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 第二步：找到微信数据库目录

微信 4.x 常见位置类似：

```text
C:\Users\<你的用户名>\Documents\xwechat_files\<你的微信ID>\db_storage
```

注意：`<你的用户名>` 和 `<你的微信ID>` 是占位符，不要把真实路径写进公开文档或提交到 GitHub。

## 第三步：解密数据库

把下面命令里的路径换成你自己的 `db_storage` 路径：

```powershell
python .\meng-wx-history-summarize-skill\scripts\recover_wechat4.py --db-dir "C:\Users\<你的用户名>\Documents\xwechat_files\<你的微信ID>\db_storage"
```

成功时会看到类似：

```text
derived and verified 21/21 database keys
decrypted=21, failed=0
```

输出会放到：

```text
outputs/decrypted_databases
.private/wechat_db_keys.json
```

`.private/wechat_db_keys.json` 是敏感文件，不要分享。

## 第四步：扫描有哪些群

```powershell
python .\meng-wx-history-summarize-skill\scripts\scan_group_chats.py --db-root outputs/decrypted_databases
```

看这个文件：

```text
outputs/group_scan/微信群聊分类清单.csv
```

在里面找到你要总结的群，复制它的 `chat_id`。`chat_id` 通常长这样：

```text
1234567890@chatroom
```

## 第五步：导出一个群

把 `<chatroom_id>` 换成上一步复制的群 ID：

```powershell
python .\meng-wx-history-summarize-skill\scripts\export_target_chat.py --db-root outputs/decrypted_databases --chat "<chatroom_id>"
```

导出文件会在：

```text
outputs/chat_exports
```

会生成 JSONL、CSV、TXT 和导出信息 JSON。它们包含聊天内容，不要上传或公开。

## 第六步：生成月报 PDF

先找到 `outputs/chat_exports` 里的 `.jsonl` 文件，然后运行：

```powershell
python .\meng-wx-history-summarize-skill\scripts\generate_monthly_report.py --input "outputs/chat_exports/<群名>.jsonl" --month 2026-06 --group "<群名>"
```

如果没有 AI 接口密钥，生成本地统计版：

```powershell
python .\meng-wx-history-summarize-skill\scripts\generate_monthly_report.py --input "outputs/chat_exports/<群名>.jsonl" --month 2026-06 --group "<群名>" --no-ai
```

结果会在：

```text
outputs/monthly_reports
```

## 第七步：生成日报

简单 Markdown 日报：

```powershell
python .\meng-wx-history-summarize-skill\scripts\summarize_daily_chat.py --input "outputs/chat_exports/<群名>.jsonl" --date 2026-06-24
```

详细 PDF 日报需要 AI 接口：

```powershell
python .\meng-wx-history-summarize-skill\scripts\generate_detailed_report.py --input "outputs/chat_exports/<群名>.jsonl" --date 2026-06-24 --group "<群名>"
```

## 常见问题

### 找不到 db_storage 怎么办？

先确认微信 PC 是 4.x 版本，并在微信设置里查看文件管理位置。通常在 `Documents\xwechat_files` 下面。

### 解密失败怎么办？

优先检查：

- `--db-dir` 是否指向 `db_storage`，不是上一级目录。
- 本机是否还有 `wx_key` 日志。
- 微信数据库是否来自当前这台机器和当前账号。

不要盲目暴力尝试密钥。脚本会用第一页 HMAC 做确定性验证。

### 月报为什么只到今天？

脚本只总结本机已经存在的聊天记录。如果今天是 6 月 24 日，`2026-06` 月报只会覆盖 6 月 1 日到 6 月 24 日，不会包含未来日期。

### 可以把结果发给别人吗？

只发你已经人工确认过、可以公开的最终 PDF 或 Markdown。不要发原始导出、数据库、密钥、完整聊天记录。

## GitHub 发布前检查

发布前运行：

```powershell
git status --short
```

确认没有这些文件：

```text
.private/
outputs/
*.db
*.db-wal
*.db-shm
*.jsonl
*.csv
*.txt
*.pdf
```

再搜索敏感词：

```powershell
rg -n "wxid_|@chatroom|wechat_db_keys|all_keys|C:\\Users|[0-9a-fA-F]{64}"
```

如果搜索结果里出现真实账号、真实群 ID、密钥或本地路径，先删掉或改成占位符。
