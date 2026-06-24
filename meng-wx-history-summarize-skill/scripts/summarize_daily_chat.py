from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_OUT_DIR = Path("outputs") / "daily_reports"
CHINA_TZ = timezone(timedelta(hours=8))


def find_latest_jsonl(directory: Path) -> Path:
    files = sorted(directory.glob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"no JSONL exports found under {directory}")
    return files[-1]


def load_messages(path: Path, target_date: str) -> list[dict[str, object]]:
    messages = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if str(row.get("time", "")).startswith(target_date + " "):
                messages.append(row)
    return messages


def build_stats(messages: list[dict[str, object]]) -> dict[str, object]:
    senders = Counter(
        str(row.get("sender_name") or "系统")
        for row in messages
        if str(row.get("sender_name") or "系统") != "系统"
    )
    kinds = Counter(str(row.get("message_kind") or "其他") for row in messages)
    hours = Counter(str(row.get("time", ""))[11:13] for row in messages)
    links = []
    files = []

    for row in messages:
        content = str(row.get("content") or "")
        raw = str(row.get("raw_content") or "")
        for url in re.findall(r"https?://[^\s<>\"']+", raw):
            if url not in links:
                links.append(url)
        if re.search(
            r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|txt|csv)(?:\s|$|\])",
            content,
            re.I,
        ):
            files.append(
                {
                    "time": row.get("time"),
                    "sender": row.get("sender_name"),
                    "content": content,
                }
            )

    return {
        "message_count": len(messages),
        "participant_count": len(senders),
        "top_senders": senders.most_common(10),
        "message_kinds": kinds.most_common(),
        "peak_hours": hours.most_common(5),
        "links": links[:20],
        "files": files[:30],
    }


def compact_text(value: object, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def message_score(row: dict[str, object]) -> int:
    content = compact_text(row.get("content"))
    score = min(len(content), 500)
    if "?" in content or "？" in content:
        score += 100
    if "http://" in content or "https://" in content:
        score += 150
    if re.search(r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar)(?:\s|$|\])", content, re.I):
        score += 180
    if str(row.get("message_kind")) in {"图片", "表情", "系统"}:
        score -= 100
    return score


def build_transcript(
    messages: list[dict[str, object]], max_chars: int
) -> tuple[str, int]:
    lines = []
    for index, row in enumerate(messages):
        content = compact_text(row.get("content"))
        lines.append(
            (
                index,
                message_score(row),
                f"[{str(row.get('time', ''))[11:16]}] "
                f"{row.get('sender_name') or '系统'} "
                f"({row.get('message_kind') or '其他'}): {content}",
            )
        )

    total = sum(len(item[2]) + 1 for item in lines)
    if total <= max_chars:
        return "\n".join(item[2] for item in lines), len(lines)

    selected: set[int] = set()
    bucket_size = max(1, len(lines) // 24)
    for start in range(0, len(lines), bucket_size):
        bucket = lines[start : start + bucket_size]
        selected.add(max(bucket, key=lambda item: item[1])[0])

    used = sum(len(lines[index][2]) + 1 for index in selected)
    for index, _, line in sorted(lines, key=lambda item: item[1], reverse=True):
        if index in selected:
            continue
        if used + len(line) + 1 > max_chars:
            continue
        selected.add(index)
        used += len(line) + 1

    chosen = [line for index, _, line in lines if index in selected]
    return "\n".join(chosen), len(chosen)


def resolve_api() -> tuple[str, str] | None:
    token = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get(
        "ANTHROPIC_AUTH_TOKEN"
    )
    if not token:
        return None

    base = (
        os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL")
        or "https://api.deepseek.com"
    ).rstrip("/")
    if base.endswith("/anthropic"):
        base = base[: -len("/anthropic")]
    return base + "/chat/completions", token


def request_ai_summary(
    messages: list[dict[str, object]],
    stats: dict[str, object],
    target_date: str,
    group_name: str,
    max_input_chars: int,
) -> tuple[str, dict[str, object]]:
    endpoint = resolve_api()
    if endpoint is None:
        raise RuntimeError("no DeepSeek API credential configured")

    transcript, selected_count = build_transcript(messages, max_input_chars)
    prompt = f"""你是商业社群聊天分析员。请仅依据下方聊天记录生成中文 Markdown 日报，不得补充记录中没有的事实。

群聊：{group_name}
日期：{target_date}
消息数：{stats['message_count']}
参与者数：{stats['participant_count']}

输出结构必须包含：
## 核心结论
用 3-6 条要点概括当天最重要的信息。

## 主要话题
按话题分组，每个话题说明讨论内容、主要参与者和可验证的结论。

## 可执行事项
列出明确行动、负责人（仅在聊天中明确时填写）、后续需要确认的问题。

## 资源与机会
汇总提到的文件、链接、产品、渠道、供应链或商业机会。

## 风险与分歧
指出尚未验证的信息、不同意见和潜在风险。

## 值得跟进的发言
列出时间、发言人和一句话原因。不要大段复制原文。

聊天记录：
{transcript}
"""

    url, token = endpoint
    model = os.environ.get("WECHAT_SUMMARY_MODEL", "deepseek-chat")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你负责严谨地总结私人商业社群记录，必须区分事实、观点和待验证信息。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 3000,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        data = json.load(response)

    content = str(data["choices"][0]["message"]["content"]).strip()
    metadata = {
        "provider": "deepseek_openai_compatible",
        "requested_model": model,
        "response_model": data.get("model"),
        "usage": data.get("usage", {}),
        "selected_messages": selected_count,
        "total_messages": len(messages),
    }
    return content, metadata


def local_summary(stats: dict[str, object]) -> str:
    senders = "\n".join(
        f"- {name}: {count} 条" for name, count in stats["top_senders"]
    ) or "- 无"
    kinds = "，".join(f"{kind} {count}" for kind, count in stats["message_kinds"])
    peaks = "，".join(f"{hour}:00 {count} 条" for hour, count in stats["peak_hours"])
    files = "\n".join(
        f"- [{item['time']}] {item['sender']}: {item['content']}"
        for item in stats["files"]
    ) or "- 未识别到文件"
    links = "\n".join(f"- {url}" for url in stats["links"]) or "- 未识别到链接"
    return f"""## 本地统计摘要

当前未生成模型语义摘要，以下内容由本地规则自动产生。

### 活跃成员
{senders}

### 消息构成
{kinds or "无"}

### 活跃时段
{peaks or "无"}

### 文件
{files}

### 链接
{links}
"""


def render_report(
    group_name: str,
    target_date: str,
    messages: list[dict[str, object]],
    stats: dict[str, object],
    body: str,
    ai_metadata: dict[str, object],
) -> str:
    top_senders = "，".join(
        f"{name} {count}条" for name, count in stats["top_senders"][:5]
    )
    kinds = "，".join(f"{kind} {count}" for kind, count in stats["message_kinds"])
    range_text = (
        f"{messages[0]['time']} 至 {messages[-1]['time']}" if messages else "无消息"
    )
    mode = "AI 语义摘要" if ai_metadata.get("provider") else "本地规则摘要"
    return f"""# {group_name} - {target_date} 聊天总结

- 消息数：{stats['message_count']}
- 参与者：{stats['participant_count']}
- 时间范围：{range_text}
- 活跃成员：{top_senders or "无"}
- 消息构成：{kinds or "无"}
- 总结模式：{mode}

{body.strip()}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an automated daily chat summary.")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--date")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--max-input-chars", type=int, default=60_000)
    parser.add_argument("--no-ai", action="store_true")
    args = parser.parse_args()

    input_path = args.input or find_latest_jsonl(Path("outputs") / "chat_exports")
    target_date = args.date or str(datetime.now(CHINA_TZ).date() - timedelta(days=1))
    group_name = input_path.stem
    messages = load_messages(input_path, target_date)
    stats = build_stats(messages)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{target_date}_{group_name}"
    report_path = args.out_dir / f"{stem}_总结.md"
    metadata_path = args.out_dir / f"{stem}_元数据.json"

    ai_metadata: dict[str, object] = {}
    if not messages:
        body = "## 结果\n\n该日期没有聊天记录。"
    elif args.no_ai:
        body = local_summary(stats)
    else:
        try:
            body, ai_metadata = request_ai_summary(
                messages,
                stats,
                target_date,
                group_name,
                args.max_input_chars,
            )
        except (RuntimeError, KeyError, urllib.error.URLError, TimeoutError) as exc:
            ai_metadata = {"error": f"{type(exc).__name__}: {exc}"}
            body = local_summary(stats)
            print(f"[WARN] AI summary unavailable, used local fallback: {exc}")

    report = render_report(
        group_name, target_date, messages, stats, body, ai_metadata
    )
    report_path.write_text(report, encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "input": str(input_path.resolve()),
                "date": target_date,
                "stats": stats,
                "ai": ai_metadata,
                "report": str(report_path.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"[DONE] date={target_date}, messages={len(messages)}, "
        f"mode={'ai' if ai_metadata.get('provider') else 'local'}"
    )
    print(f"[OK] report: {report_path.resolve()}")
    print(f"[OK] metadata: {metadata_path.resolve()}")


if __name__ == "__main__":
    main()
