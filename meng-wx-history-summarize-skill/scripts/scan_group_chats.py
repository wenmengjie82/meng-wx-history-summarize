from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_DB_ROOT = Path("outputs") / "decrypted_databases"
DEFAULT_OUT_DIR = Path("outputs") / "group_scan"
CHINA_TZ = timezone(timedelta(hours=8))


CATEGORY_RULES: list[tuple[str, list[str]]] = [
    (
        "跨境电商/TikTok/出海",
        [
            "tk",
            "tiktok",
            "跨境",
            "出海",
            "亚马逊",
            "amazon",
            "amz",
            "独立站",
            "shopify",
            "temu",
            "shein",
            "卖家",
            "货盘",
            "选品",
            "电商",
            "海外",
            "affiliate",
            "联盟",
        ],
    ),
    (
        "营销/投流/内容/达人",
        [
            "广告",
            "投流",
            "营销",
            "达人",
            "短视频",
            "直播",
            "拍摄",
            "素材",
            "kol",
            "私域",
            "小红书",
            "内容",
            "矩阵",
            "mcn",
            "红人",
            "ip",
            "种草",
            "affiliate",
        ],
    ),
    (
        "AI/工具/SaaS/自动化",
        [
            "ai",
            "gpt",
            "claude",
            "codex",
            "自动化",
            "工具",
            "adspower",
            "领星",
            "erp",
            "saas",
            "系统",
            "软件",
            "agent",
            "openclaw",
            "clawdbot",
            "waytoagi",
            "大模型",
            "全栈",
            "datawhale",
            "python",
            "飞书",
            "多维表格",
        ],
    ),
    (
        "校园/校友/组织",
        [
            "大学",
            "校友",
            "校园",
            "毕业",
            "党员",
            "党校",
            "积极分子",
            "支部",
            "团支部",
            "勤工",
            "菜鸟驿站",
            "学生",
            "老师",
            "小学",
            "高翻",
            "男篮",
            "志愿",
            "volunteer",
        ],
    ),
    (
        "课程/活动/社群学习",
        [
            "训练营",
            "课程",
            "课堂",
            "峰会",
            "活动",
            "沙龙",
            "分享",
            "研讨",
            "大会",
            "公开课",
            "学习",
            "共学",
            "班",
            "营",
            "speech club",
            "集训",
            "培训",
        ],
    ),
    (
        "供应链/物流/货源",
        [
            "供应链",
            "工厂",
            "货代",
            "海外仓",
            "仓",
            "物流",
            "清关",
            "采购",
            "生产",
            "样品",
            "印刷",
            "图书",
            "白酒",
        ],
    ),
    (
        "餐饮/外卖/本地服务",
        [
            "外卖",
            "订餐",
            "点餐",
            "餐饮",
            "餐厅",
            "饭",
            "厨房",
            "厨",
            "凑饭",
            "快餐",
            "工作餐",
            "园区",
            "快餐",
            "送餐",
            "瑞幸",
            "咖啡",
            "火锅",
            "喝了吗",
            "小火炉",
        ],
    ),
    (
        "医美/健康/个护",
        [
            "医美",
            "美莱",
            "安肤",
            "凝肌",
            "beautypark",
            "个护",
            "护肤",
            "美容",
            "健康",
        ],
    ),
    (
        "舞蹈/运动/兴趣",
        [
            "舞",
            "爵士",
            "yog",
            "瑜伽",
            "健身",
            "运动",
            "外拍",
            "跟拍",
            "摄影",
            "舞界线",
            "皇家舞蹈团",
            "kpop",
            "popstar",
        ],
    ),
    (
        "娱乐/游戏/聚会",
        [
            "狼人杀",
            "游戏",
            "派对",
            "party",
            "年会",
            "聚会",
            "美少女",
            "搞事",
            "冲冲冲",
            "喝酒",
            "火锅",
            "结婚",
        ],
    ),
    (
        "团购/消费/福利",
        [
            "团购",
            "好物",
            "集市",
            "福利",
            "捡漏",
            "优惠",
            "会员",
            "粉丝",
            "真爱粉",
            "vip",
        ],
    ),
    (
        "创业/赚钱/商业",
        [
            "创业",
            "赚钱",
            "商业",
            "增长",
            "老板",
            "生意",
            "变现",
            "私董会",
            "聊赚钱",
        ],
    ),
    (
        "项目/客户/工作协作",
        [
            "沟通",
            "项目",
            "对接",
            "客户",
            "服务",
            "交付",
            "合同",
            "合作",
            "拍摄沟通",
            "工作",
            "会议",
            "业务",
            "团队",
            "战队",
            "策划",
            "排查",
            "部署",
            "内部",
            "外部",
            "展厅",
            "搭建",
            "设计",
            "转正",
        ],
    ),
    (
        "生活/亲友/同学",
        [
            "家",
            "亲",
            "朋友",
            "同学",
            "同事",
            "聚餐",
            "dinner",
            "旅行",
            "小区",
            "邻居",
            "生活",
            "生日",
            "贵州游",
            "结婚",
            "后盾",
            "家庭",
        ],
    ),
]


def ts_text(value: int | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value), CHINA_TZ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (OSError, OverflowError, ValueError):
        return ""


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or "微信群聊分类"


def md_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", r"\|")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def load_contacts(db_root: Path) -> dict[str, dict[str, object]]:
    contact_db = db_root / "contact" / "contact.db"
    groups: dict[str, dict[str, object]] = {}
    if not contact_db.exists():
        return groups

    with connect(contact_db) as conn:
        query = """
            SELECT username, remark, nick_name, alias, local_type, flag, delete_flag,
                   chat_room_notify, is_in_chat_room, chat_room_type
            FROM contact
            WHERE username LIKE '%@chatroom'
        """
        for row in conn.execute(query):
            username = str(row["username"])
            display = (
                str(row["remark"] or "")
                or str(row["nick_name"] or "")
                or str(row["alias"] or "")
                or username
            )
            groups.setdefault(username, {"chat_id": username})
            groups[username].update(
                {
                    "name": display,
                    "remark": str(row["remark"] or ""),
                    "nickname": str(row["nick_name"] or ""),
                    "local_type": int(row["local_type"] or 0),
                    "flag": int(row["flag"] or 0),
                    "delete_flag": int(row["delete_flag"] or 0),
                    "chat_room_notify": int(row["chat_room_notify"] or 0),
                    "is_in_chat_room": int(row["is_in_chat_room"] or 0),
                    "chat_room_type": int(row["chat_room_type"] or 0),
                    "sources": {"contact"},
                }
            )

        room_id_to_username = {
            int(row["id"]): str(row["username"])
            for row in conn.execute("SELECT id, username FROM chat_room")
        }
        for username in room_id_to_username.values():
            groups.setdefault(username, {"chat_id": username, "name": username})
            groups[username].setdefault("sources", set()).add("chat_room")

        member_counts = Counter(
            {
                room_id_to_username.get(int(row["room_id"]), ""): int(row["count"])
                for row in conn.execute(
                    "SELECT room_id, COUNT(*) AS count FROM chatroom_member GROUP BY room_id"
                )
            }
        )
        member_counts.pop("", None)
        for username, count in member_counts.items():
            groups.setdefault(username, {"chat_id": username, "name": username})
            groups[username]["member_count"] = count

    return groups


def merge_sessions(db_root: Path, groups: dict[str, dict[str, object]]) -> None:
    session_db = db_root / "session" / "session.db"
    if not session_db.exists():
        return
    with connect(session_db) as conn:
        query = """
            SELECT username, unread_count, is_hidden, summary, last_timestamp,
                   sort_timestamp, last_msg_type, last_sender_display_name
            FROM SessionTable
            WHERE username LIKE '%@chatroom'
        """
        for row in conn.execute(query):
            username = str(row["username"])
            groups.setdefault(username, {"chat_id": username, "name": username})
            groups[username].setdefault("sources", set()).add("session")
            groups[username].update(
                {
                    "unread_count": int(row["unread_count"] or 0),
                    "is_hidden": int(row["is_hidden"] or 0),
                    "session_summary": str(row["summary"] or ""),
                    "session_last_timestamp": int(row["last_timestamp"] or 0),
                    "session_sort_timestamp": int(row["sort_timestamp"] or 0),
                    "last_msg_type": int(row["last_msg_type"] or 0),
                    "last_sender_display_name": str(row["last_sender_display_name"] or ""),
                }
            )


def merge_message_index(db_root: Path, groups: dict[str, dict[str, object]]) -> None:
    message_dir = db_root / "message"
    if not message_dir.exists():
        return
    message_dbs = sorted(
        path
        for path in message_dir.glob("message_*.db")
        if re.fullmatch(r"message_\d+\.db", path.name)
    )

    for db_path in message_dbs:
        with connect(db_path) as conn:
            group_names = [
                str(row["user_name"])
                for row in conn.execute(
                    "SELECT user_name FROM Name2Id WHERE user_name LIKE '%@chatroom'"
                )
            ]
            for username in group_names:
                groups.setdefault(username, {"chat_id": username, "name": username})
                groups[username].setdefault("sources", set()).add(db_path.name)

            existing_tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                )
            }
            for username in group_names:
                table = "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()
                if table not in existing_tables:
                    continue
                stats = conn.execute(
                    f'SELECT COUNT(*) AS count, MIN(create_time) AS first_ts, '
                    f'MAX(create_time) AS last_ts FROM "{table}"'
                ).fetchone()
                item = groups[username]
                item["message_count"] = int(item.get("message_count", 0)) + int(
                    stats["count"] or 0
                )
                first_ts = int(stats["first_ts"] or 0)
                last_ts = int(stats["last_ts"] or 0)
                if first_ts and (
                    not item.get("first_message_ts")
                    or first_ts < int(item["first_message_ts"])
                ):
                    item["first_message_ts"] = first_ts
                if last_ts and (
                    not item.get("last_message_ts")
                    or last_ts > int(item["last_message_ts"])
                ):
                    item["last_message_ts"] = last_ts
                shard_counts = item.setdefault("message_shards", {})
                shard_counts[db_path.name] = int(stats["count"] or 0)


def classify_group(item: dict[str, object]) -> str:
    name = str(item.get("name") or item.get("chat_id") or "")
    summary = str(item.get("session_summary") or "")
    scores: Counter[str] = Counter()
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            lowered = keyword.lower()
            if lowered in name.lower():
                scores[category] += 3
            if lowered in summary.lower():
                scores[category] += 1
    if scores:
        return scores.most_common(1)[0][0]
    if int(item.get("member_count") or 0) <= 5 and int(item.get("message_count") or 0) < 200:
        return "小群/临时群"
    return "未分类"


def activity_tier(last_ts: int, now_ts: int) -> str:
    if not last_ts:
        return "无消息记录"
    days = (now_ts - last_ts) / 86400
    if days <= 7:
        return "近7天活跃"
    if days <= 30:
        return "近30天活跃"
    if days <= 90:
        return "近90天活跃"
    if days <= 365:
        return "一年内历史群"
    return "长期未活跃"


def finalize(groups: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    now_ts = int(datetime.now(CHINA_TZ).timestamp())
    rows: list[dict[str, object]] = []
    for item in groups.values():
        last_ts = max(
            int(item.get("last_message_ts") or 0),
            int(item.get("session_last_timestamp") or 0),
            int(item.get("session_sort_timestamp") or 0),
        )
        first_ts = int(item.get("first_message_ts") or 0)
        sources = item.get("sources") or set()
        if isinstance(sources, set):
            source_text = ",".join(sorted(sources))
        else:
            source_text = str(sources)
        row = {
            "category": classify_group(item),
            "activity": activity_tier(last_ts, now_ts),
            "name": str(item.get("name") or item.get("chat_id") or ""),
            "chat_id": str(item.get("chat_id") or ""),
            "member_count": int(item.get("member_count") or 0),
            "message_count": int(item.get("message_count") or 0),
            "first_message_time": ts_text(first_ts),
            "last_active_time": ts_text(last_ts),
            "unread_count": int(item.get("unread_count") or 0),
            "is_hidden": int(item.get("is_hidden") or 0),
            "delete_flag": int(item.get("delete_flag") or 0),
            "sources": source_text,
        }
        rows.append(row)

    rows.sort(
        key=lambda row: (
            row["category"],
            -(datetime.strptime(row["last_active_time"], "%Y-%m-%d %H:%M:%S").timestamp()
              if row["last_active_time"] else 0),
            -int(row["message_count"]),
            row["name"],
        )
    )
    return rows


def write_outputs(rows: list[dict[str, object]], out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "微信群聊分类清单.csv"
    json_path = out_dir / "微信群聊分类清单.json"
    md_path = out_dir / "微信群聊分类报告.md"

    fieldnames = [
        "category",
        "activity",
        "name",
        "chat_id",
        "member_count",
        "message_count",
        "first_message_time",
        "last_active_time",
        "unread_count",
        "is_hidden",
        "delete_flag",
        "sources",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "generated_at": datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "total_groups": len(rows),
        "category_counts": dict(Counter(row["category"] for row in rows)),
        "activity_counts": dict(Counter(row["activity"] for row in rows)),
        "rows": rows,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# 微信群聊分类报告",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 群聊总数：{len(rows)}",
        f"- 有消息记录群：{sum(1 for row in rows if row['message_count'])}",
        f"- 近7天活跃群：{sum(1 for row in rows if row['activity'] == '近7天活跃')}",
        "",
        "## 分类统计",
        "",
        "| 分类 | 数量 | 有消息记录 | 近7天活跃 |",
        "|---|---:|---:|---:|",
    ]
    for category, count in Counter(row["category"] for row in rows).most_common():
        subset = [row for row in rows if row["category"] == category]
        lines.append(
            f"| {md_cell(category)} | {count} | "
            f"{sum(1 for row in subset if row['message_count'])} | "
            f"{sum(1 for row in subset if row['activity'] == '近7天活跃')} |"
        )

    lines.extend(
        [
            "",
            "## 近7天活跃群 TOP 30",
            "",
            "| 分类 | 群名 | 成员数 | 消息数 | 最近活跃 |",
            "|---|---|---:|---:|---|",
        ]
    )
    active = [
        row for row in rows if row["activity"] == "近7天活跃" and row["message_count"]
    ]
    active.sort(key=lambda row: (row["last_active_time"], row["message_count"]), reverse=True)
    for row in active[:30]:
        lines.append(
            f"| {md_cell(row['category'])} | {md_cell(row['name'])} | {row['member_count']} | "
            f"{row['message_count']} | {row['last_active_time']} |"
        )

    lines.extend(
        [
            "",
            "## 各分类消息量 TOP 10",
            "",
        ]
    )
    for category in sorted(set(row["category"] for row in rows)):
        subset = [row for row in rows if row["category"] == category]
        subset.sort(key=lambda row: int(row["message_count"]), reverse=True)
        lines.extend(
            [
                f"### {md_cell(category)}",
                "",
                "| 群名 | 活跃度 | 成员数 | 消息数 | 最近活跃 |",
                "|---|---|---:|---:|---|",
            ]
        )
        for row in subset[:10]:
            lines.append(
                f"| {md_cell(row['name'])} | {row['activity']} | {row['member_count']} | "
                f"{row['message_count']} | {row['last_active_time']} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 说明",
            "",
            "- 分类依据：群名和会话摘要中的关键词；群名命中权重更高，不读取或上传完整聊天正文。",
            "- 消息数依据：已解密 `message_*.db` 中与群 ID MD5 对应的 `Msg_*` 表。",
            "- 成员数依据：`contact.db` 的 `chatroom_member` 表；部分历史群可能缺成员数。",
            "- 全量清单见同目录 CSV/JSON。",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"markdown": md_path, "csv": csv_path, "json": json_path}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Scan and classify WeChat group chats.")
    parser.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    groups = load_contacts(args.db_root)
    merge_sessions(args.db_root, groups)
    merge_message_index(args.db_root, groups)
    rows = finalize(groups)
    paths = write_outputs(rows, args.out_dir)

    categories = Counter(row["category"] for row in rows)
    activities = Counter(row["activity"] for row in rows)
    print(f"[DONE] groups={len(rows)}")
    print("[INFO] categories=" + json.dumps(categories, ensure_ascii=False))
    print("[INFO] activities=" + json.dumps(activities, ensure_ascii=False))
    for key, path in paths.items():
        print(f"[OK] {key}: {path.resolve()}")


if __name__ == "__main__":
    main()
