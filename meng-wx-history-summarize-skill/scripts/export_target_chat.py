from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import zstandard as zstd


DEFAULT_DB_ROOT = Path("outputs") / "decrypted_databases"
DEFAULT_OUT_DIR = Path("outputs") / "chat_exports"
CHINA_TZ = timezone(timedelta(hours=8))

MESSAGE_KIND = {
    1: "文本",
    3: "图片",
    34: "语音",
    42: "名片",
    43: "视频",
    47: "表情",
    48: "位置",
    10000: "系统",
}

_ZSTD = zstd.ZstdDecompressor()


def decode_content(value: object, compression_type: int) -> tuple[str, str]:
    if value is None:
        return "", ""
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        try:
            if compression_type == 4:
                value = _ZSTD.decompress(value)
            return value.decode("utf-8", errors="replace"), ""
        except Exception as exc:
            return "", f"{type(exc).__name__}: {exc}"
    return str(value), ""


def split_group_sender(content: str) -> tuple[str, str]:
    if ":\n" in content:
        sender, body = content.split(":\n", 1)
        if re.fullmatch(r"[A-Za-z0-9_\-@.]+", sender):
            return sender, body

    match = re.match(
        r"^([A-Za-z0-9_\-@.]+):(<\?xml|<msg|<msglist|<voipmsg|<sysmsg)",
        content,
    )
    if match:
        sender = match.group(1)
        return sender, content[len(sender) + 1 :]
    return "", content


def clean_xml_text(value: str | None) -> str:
    if not value:
        return ""
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def first_text(root: ET.Element, paths: list[str]) -> str:
    for path in paths:
        value = clean_xml_text(root.findtext(path))
        if value:
            return value
    return ""


def extract_xml_sender(content: str) -> str:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        match = re.search(
            r'\bfromusername\s*=\s*["\']([^"\']+)["\']', content, re.I
        )
        return clean_xml_text(match.group(1)) if match else ""

    direct = clean_xml_text(root.findtext("fromusername"))
    if direct:
        return direct

    for tag in ("videomsg", "img", "voicemsg", "emoji"):
        node = root.find(f".//{tag}")
        if node is not None:
            sender = clean_xml_text(node.get("fromusername"))
            if sender:
                return sender

    pat_sender = clean_xml_text(root.findtext(".//patinfo/fromusername"))
    if pat_sender:
        return pat_sender

    for node in root.findall(".//fromusername"):
        sender = clean_xml_text(node.text)
        if sender:
            return sender
    return ""


def summarize_xml(content: str, kind: str) -> str:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        title_match = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", content, re.S)
        if title_match:
            return clean_xml_text(title_match.group(1))
        return f"[{kind}]"

    appmsg = root.find(".//appmsg")
    if appmsg is not None:
        title = first_text(appmsg, ["title", ".//title"])
        description = first_text(appmsg, ["des", ".//des"])
        app_type = clean_xml_text(appmsg.findtext("type"))
        reference_name = first_text(appmsg, [".//refermsg/displayname"])
        reference_text = first_text(appmsg, [".//refermsg/content"])

        parts = []
        if title:
            parts.append(title)
        elif description:
            parts.append(description)
        else:
            parts.append(f"[应用消息{':' + app_type if app_type else ''}]")
        if reference_text:
            prefix = f"{reference_name}: " if reference_name else ""
            parts.append(f"[引用 {prefix}{reference_text}]")
        return " ".join(parts)

    if root.find(".//img") is not None:
        return "[图片]"
    if root.find(".//voicemsg") is not None:
        return "[语音]"
    if root.find(".//videomsg") is not None:
        return "[视频]"
    if root.find(".//emoji") is not None:
        return "[表情]"
    if root.find(".//location") is not None:
        location = root.find(".//location")
        label = clean_xml_text(location.get("label") if location is not None else "")
        return f"[位置] {label}".strip()

    text = clean_xml_text(" ".join(root.itertext()))
    return text or f"[{kind}]"


def readable_content(content: str, local_type: int) -> tuple[str, str]:
    sender, body = split_group_sender(content)
    kind = MESSAGE_KIND.get(local_type, "消息")
    stripped = body.strip()

    if not stripped:
        return sender, f"[{kind}]"
    if stripped.startswith("<") and stripped.endswith(">"):
        return sender or extract_xml_sender(stripped), summarize_xml(stripped, kind)
    if local_type in MESSAGE_KIND and local_type != 1 and stripped == body.strip():
        if local_type == 10000:
            return sender, clean_xml_text(re.sub(r"<[^>]+>", " ", stripped))
    return sender, body


def load_contacts(contact_db: Path, chat: str) -> tuple[dict[str, str], str]:
    names: dict[str, str] = {}
    group_name = ""
    if not contact_db.exists():
        return names, group_name

    with sqlite3.connect(contact_db) as conn:
        for username, remark, nickname, alias in conn.execute(
            "SELECT username, remark, nick_name, alias FROM contact"
        ):
            display = remark or nickname or alias or username
            if username:
                names[str(username)] = str(display)

        row = conn.execute(
            "SELECT remark, nick_name FROM contact WHERE username = ?",
            (chat,),
        ).fetchone()
        if row:
            group_name = str(row[1] or row[0] or "")
    return names, group_name


def load_name2id(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        int(rowid): str(username or "")
        for rowid, username in conn.execute("SELECT rowid, user_name FROM Name2Id")
    }


def find_message_databases(db_root: Path, table_name: str) -> list[Path]:
    result = []
    for path in sorted((db_root / "message").glob("message_*.db")):
        with sqlite3.connect(path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
        if exists:
            result.append(path)
    return result


def collect_messages(
    db_files: list[Path],
    table_name: str,
    contact_names: dict[str, str],
) -> tuple[list[dict[str, object]], Counter[str]]:
    messages: list[dict[str, object]] = []
    stats: Counter[str] = Counter()

    query = f"""
        SELECT local_id, server_id, local_type, sort_seq, real_sender_id,
               create_time, status, upload_status, download_status, server_seq,
               message_content, WCDB_CT_message_content
        FROM "{table_name}"
    """

    for db_path in db_files:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            sender_ids = load_name2id(conn)
            for row in conn.execute(query):
                raw, decode_error = decode_content(
                    row["message_content"], int(row["WCDB_CT_message_content"] or 0)
                )
                prefix_sender, display_content = readable_content(
                    raw, int(row["local_type"] or 0)
                )
                mapped_sender = sender_ids.get(int(row["real_sender_id"] or 0), "")
                sender_username = prefix_sender or mapped_sender
                if int(row["local_type"] or 0) == 10000 and not prefix_sender:
                    sender_username = ""
                sender_name = contact_names.get(sender_username, sender_username or "系统")
                timestamp = int(row["create_time"] or 0)

                if decode_error:
                    stats["decode_errors"] += 1
                stats[f"type:{int(row['local_type'] or 0)}"] += 1

                messages.append(
                    {
                        "time": datetime.fromtimestamp(timestamp, CHINA_TZ).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "create_time": timestamp,
                        "sender_username": sender_username,
                        "sender_name": sender_name,
                        "content": display_content,
                        "raw_content": raw,
                        "local_type": int(row["local_type"] or 0),
                        "message_kind": MESSAGE_KIND.get(
                            int(row["local_type"] or 0), "其他"
                        ),
                        "server_id": int(row["server_id"] or 0),
                        "server_seq": int(row["server_seq"] or 0),
                        "sort_seq": int(row["sort_seq"] or 0),
                        "local_id": int(row["local_id"] or 0),
                        "real_sender_id": int(row["real_sender_id"] or 0),
                        "status": int(row["status"] or 0),
                        "upload_status": int(row["upload_status"] or 0),
                        "download_status": int(row["download_status"] or 0),
                        "source_db": db_path.name,
                        "decode_error": decode_error,
                    }
                )

    messages.sort(
        key=lambda item: (
            item["create_time"],
            item["sort_seq"],
            item["server_seq"],
            item["source_db"],
            item["local_id"],
        )
    )

    deduplicated = []
    seen: set[tuple[object, ...]] = set()
    for item in messages:
        server_id = int(item["server_id"])
        if server_id:
            key = ("server", server_id)
        else:
            key = (
                "local",
                item["source_db"],
                item["local_id"],
                item["create_time"],
            )
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)
        deduplicated.append(item)

    return deduplicated, stats


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or "微信群聊"


def write_exports(
    messages: list[dict[str, object]],
    stats: Counter[str],
    out_dir: Path,
    chat: str,
    group_name: str,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(group_name or chat)
    paths = {
        "jsonl": out_dir / f"{stem}.jsonl",
        "csv": out_dir / f"{stem}.csv",
        "txt": out_dir / f"{stem}.txt",
        "summary": out_dir / f"{stem}_导出信息.json",
    }

    with paths["jsonl"].open("w", encoding="utf-8", newline="\n") as handle:
        for item in messages:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    csv_fields = [
        "time",
        "sender_name",
        "sender_username",
        "message_kind",
        "local_type",
        "content",
        "server_id",
        "source_db",
        "local_id",
    ]
    with paths["csv"].open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(messages)

    with paths["txt"].open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"群聊：{group_name or chat}\n")
        handle.write(f"群ID：{chat}\n")
        handle.write(f"消息数：{len(messages)}\n")
        if messages:
            handle.write(f"时间范围：{messages[0]['time']} 至 {messages[-1]['time']}\n")
        handle.write("=" * 72 + "\n\n")
        for item in messages:
            content = str(item["content"]).replace("\r\n", "\n").replace("\r", "\n")
            indented = content.replace("\n", "\n    ")
            handle.write(
                f"[{item['time']}] {item['sender_name']} "
                f"({item['message_kind']}):\n    {indented}\n\n"
            )

    summary = {
        "chat": chat,
        "group_name": group_name,
        "message_count": len(messages),
        "start_time": messages[0]["time"] if messages else None,
        "end_time": messages[-1]["time"] if messages else None,
        "decode_errors": stats["decode_errors"],
        "duplicates_removed": stats["duplicates"],
        "message_types": {
            key.removeprefix("type:"): value
            for key, value in sorted(stats.items())
            if key.startswith("type:")
        },
        "files": {key: str(path.resolve()) for key, path in paths.items() if key != "summary"},
    }
    paths["summary"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return paths


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        description="Export a decrypted WeChat 4.x chat to JSONL, CSV and text."
    )
    parser.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--chat", required=True, help="Target chatroom id, e.g. xxxxx@chatroom")
    args = parser.parse_args()

    table_name = "Msg_" + hashlib.md5(args.chat.encode("utf-8")).hexdigest()
    contact_names, default_group_name = load_contacts(
        args.db_root / "contact/contact.db", args.chat
    )
    group_name = default_group_name or contact_names.get(args.chat, args.chat)
    db_files = find_message_databases(args.db_root, table_name)
    if not db_files:
        raise RuntimeError(f"message table not found: {table_name}")

    print(f"[INFO] chat={args.chat}, name={group_name}, table={table_name}")
    print(f"[INFO] shards={', '.join(path.name for path in db_files)}")
    messages, stats = collect_messages(db_files, table_name, contact_names)
    paths = write_exports(messages, stats, args.out_dir, args.chat, group_name)

    print(
        f"[DONE] messages={len(messages)}, decode_errors={stats['decode_errors']}, "
        f"duplicates_removed={stats['duplicates']}"
    )
    if messages:
        print(f"[INFO] range={messages[0]['time']} -> {messages[-1]['time']}")
    for kind, path in paths.items():
        print(f"[OK] {kind}: {path.resolve()}")


if __name__ == "__main__":
    main()
