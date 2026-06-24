from __future__ import annotations

import argparse
import html
import json
import os
import re
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


DEFAULT_OUT_DIR = Path("outputs") / "monthly_reports"
CHINA_TZ = timezone(timedelta(hours=8))

NAVY = colors.HexColor("#26313F")
BLUE = colors.HexColor("#3265A8")
SLATE = colors.HexColor("#667085")
AMBER = colors.HexColor("#B7791F")
EMERALD = colors.HexColor("#0F766E")
RED = colors.HexColor("#B42318")
PAPER = colors.HexColor("#FBFCFE")
CARD = colors.HexColor("#F5F7FA")
BORDER = colors.HexColor("#D9DEE8")
TRACK = colors.HexColor("#E8ECF3")


def register_fonts() -> None:
    pdfmetrics.registerFont(
        TTFont("MicrosoftYaHei", r"C:\Windows\Fonts\msyh.ttc", subfontIndex=0)
    )
    pdfmetrics.registerFont(
        TTFont("MicrosoftYaHei-Bold", r"C:\Windows\Fonts\msyhbd.ttc", subfontIndex=0)
    )
    pdfmetrics.registerFontFamily(
        "MicrosoftYaHei",
        normal="MicrosoftYaHei",
        bold="MicrosoftYaHei-Bold",
    )


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=False).replace("\n", "<br/>")


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or "微信群聊月报"


def month_bounds(month: str) -> tuple[str, str]:
    start = datetime.strptime(month + "-01", "%Y-%m-%d")
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    return start.strftime("%Y-%m-%d "), next_month.strftime("%Y-%m-%d ")


def load_month_messages(path: Path, month: str) -> list[dict[str, object]]:
    start_text, end_text = month_bounds(month)
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            time_text = str(row.get("time") or "")
            if start_text <= time_text < end_text:
                rows.append(row)
    rows.sort(
        key=lambda row: (
            int(row.get("create_time") or 0),
            int(row.get("sort_seq") or 0),
            int(row.get("server_seq") or 0),
        )
    )
    if not rows:
        raise RuntimeError(f"no messages found for month {month}")
    return rows


def find_latest_jsonl(directory: Path) -> Path:
    files = sorted(directory.glob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"no JSONL exports found under {directory}")
    return files[-1]


def classify_messages(messages: list[dict[str, object]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in messages:
        raw = str(row.get("raw_content") or "")
        local_type = int(row.get("local_type") or 0)
        kind = str(row.get("message_kind") or "其他")
        content = str(row.get("content") or "")
        if "<refermsg>" in raw:
            counts["引用消息"] += 1
        elif local_type == 1:
            counts["文本消息"] += 1
        elif kind == "图片":
            counts["图片消息"] += 1
        elif kind == "表情":
            counts["动画表情"] += 1
        elif kind == "系统":
            counts["系统消息"] += 1
        elif kind == "视频":
            counts["视频消息"] += 1
        elif kind == "语音":
            counts["语音消息"] += 1
        elif "http://" in content or "https://" in content:
            counts["链接消息"] += 1
        else:
            counts["其他消息"] += 1
    return counts


def build_stats(messages: list[dict[str, object]]) -> dict[str, object]:
    senders = Counter(
        str(row.get("sender_name") or "系统")
        for row in messages
        if str(row.get("sender_name") or "系统") != "系统"
    )
    daily = Counter(str(row.get("time") or "")[:10] for row in messages)
    hourly = Counter(str(row.get("time") or "")[11:13] for row in messages)
    return {
        "message_count": len(messages),
        "participant_count": len(senders),
        "active_days": len(daily),
        "start_time": messages[0]["time"],
        "end_time": messages[-1]["time"],
        "top_senders": senders.most_common(12),
        "daily_counts": dict(sorted(daily.items())),
        "peak_days": daily.most_common(8),
        "peak_hours": [hour + ":00" for hour, _ in hourly.most_common(5)],
        "message_types": dict(classify_messages(messages).most_common()),
    }


THEME_RULES: list[tuple[str, list[str]]] = [
    ("商业模式/赚钱路径", ["赚钱", "变现", "商业模式", "副业", "收入", "利润", "成交", "付费", "收费"]),
    ("AI/工具/自动化", ["ai", "agent", "gpt", "claude", "cursor", "自动化", "工具", "模型", "工作流"]),
    ("内容/IP/流量", ["内容", "小红书", "抖音", "视频", "直播", "ip", "流量", "矩阵", "账号", "粉丝"]),
    ("课程/社群/线下", ["课程", "社群", "线下", "训练营", "公开课", "活动", "报名", "学员"]),
    ("产品/项目/交付", ["产品", "项目", "交付", "需求", "客户", "方案", "服务", "报价"]),
    ("招聘/合作/资源", ["招聘", "合作", "招募", "资源", "对接", "合伙", "兼职", "岗位"]),
    ("出海/跨境/海外", ["出海", "跨境", "海外", "tiktok", "amazon", "独立站", "shopify"]),
]


def keyword_counts(messages: list[dict[str, object]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in messages:
        text = str(row.get("content") or "").lower()
        for theme, keywords in THEME_RULES:
            if any(keyword.lower() in text for keyword in keywords):
                counts[theme] += 1
    return counts


def clean_content(row: dict[str, object], limit: int = 360) -> str:
    content = re.sub(r"\s+", " ", str(row.get("content") or "")).strip()
    if len(content) > limit:
        content = content[: limit - 1] + "…"
    return content


def transcript(messages: list[dict[str, object]]) -> str:
    lines = []
    for row in messages:
        content = clean_content(row)
        if not content:
            continue
        lines.append(
            f"[{str(row.get('time'))[:16]}] "
            f"{row.get('sender_name') or '系统'} "
            f"({row.get('message_kind') or '其他'}): {content}"
        )
    return "\n".join(lines)


def chunk_messages(
    messages: list[dict[str, object]], max_chars: int = 18000, max_messages: int = 280
) -> list[list[dict[str, object]]]:
    chunks: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_chars = 0
    for row in messages:
        estimated = len(clean_content(row)) + 80
        if current and (
            current_chars + estimated > max_chars or len(current) >= max_messages
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(row)
        current_chars += estimated
    if current:
        chunks.append(current)
    return chunks


def resolve_api() -> tuple[str, str]:
    token = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not token:
        raise RuntimeError("AI API credential is not configured")
    base = (
        os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL")
        or "https://api.deepseek.com"
    ).rstrip("/")
    if base.endswith("/anthropic"):
        base = base[: -len("/anthropic")]
    return base + "/chat/completions", token


def extract_json(text: str) -> dict[str, object]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            candidate = match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    return json.loads(candidate, strict=False)
                except json.JSONDecodeError:
                    cleaned = "".join(
                        ch if ch in "\n\r\t" or ord(ch) >= 32 else " "
                        for ch in candidate
                    )
                    return json.loads(cleaned, strict=False)
        raise


def request_json(prompt: str, max_tokens: int) -> tuple[dict[str, object], dict[str, object]]:
    endpoint, token = resolve_api()
    payload = {
        "model": os.environ.get("WECHAT_SUMMARY_MODEL", "deepseek-chat"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是严谨的中文商业社群分析师。只依据用户提供的聊天记录总结，"
                    "必须区分事实、群友观点、机会、风险和待验证事项。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.15,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        result = json.load(response)
    content = str(result["choices"][0]["message"]["content"])
    return extract_json(content), {
        "model": payload["model"],
        "usage": result.get("usage", {}),
    }


def summarize_chunk(
    chunk: list[dict[str, object]], group: str, month: str, index: int, total: int
) -> tuple[dict[str, object], dict[str, object]]:
    prompt = f"""请把下面这段微信群 6 月聊天记录提炼成结构化素材，供最终月报合并使用。

群聊：{group}
月份：{month}
分块：{index}/{total}
消息范围：{chunk[0]['time']} 至 {chunk[-1]['time']}
消息数：{len(chunk)}

必须输出 JSON，不要 Markdown 代码块。结构如下：
{{
  "period": "本分块时间范围",
  "core_topics": [{{"title": "话题", "summary": "事实性摘要", "evidence": "来自聊天的依据，不要长引原文"}}],
  "business_opportunities": [{{"title": "机会", "detail": "机会内容", "next_step": "可执行下一步"}}],
  "risks": [{{"title": "风险", "why": "原因", "mitigation": "规避建议"}}],
  "actions": [{{"title": "行动", "steps": ["步骤1", "步骤2"]}}],
  "resources": [{{"title": "资源/工具/人脉/活动", "detail": "用途或限制"}}],
  "questions": [{{"question": "真实出现或仍待确认的问题", "answer": "基于聊天的回答；没有答案则写待验证"}}],
  "quotes": [{{"quote": "45字以内真实原话", "speaker": "发言人", "meaning": "为什么重要"}}],
  "signals": ["值得在月报里保留的趋势信号"]
}}

要求：
- 不要补充聊天中没有的事实。
- 同类话题合并，不要把每条消息都改写成一条。
- 原话引用每条不超过45字；没有高价值原话可以少写。
- 对金额、成绩、承诺、市场判断写明是群友观点或待验证。

聊天记录：
{transcript(chunk)}
"""
    return request_json(prompt, max_tokens=2600)


def merge_ai_summaries(
    chunk_summaries: list[dict[str, object]],
    stats: dict[str, object],
    group: str,
    month: str,
) -> tuple[dict[str, object], dict[str, object]]:
    compact = json.dumps(chunk_summaries, ensure_ascii=False)
    prompt = f"""请把以下分块素材合并成一份中文商业社群月报 JSON。

群聊：{group}
月份：{month}
统计：
- 消息数：{stats['message_count']}
- 参与人数：{stats['participant_count']}
- 活跃天数：{stats['active_days']}
- 时间范围：{stats['start_time']} 至 {stats['end_time']}
- 活跃时段：{'、'.join(stats['peak_hours'])}

输出 JSON，不要 Markdown 代码块，结构如下：
{{
  "executive_summary": ["5-7条月度结论，每条60-120字"],
  "month_themes": [
    {{"title": "主题", "summary": "本月讨论脉络", "evidence": "基于哪些群内讨论", "action": "建议动作"}}
  ],
  "opportunities": [
    {{"title": "机会", "detail": "机会说明", "next_step": "下一步", "confidence": "高/中/低，说明依据"}}
  ],
  "risks": [
    {{"title": "风险", "why": "风险来源", "mitigation": "规避建议"}}
  ],
  "playbook": [
    {{"title": "可执行打法", "steps": ["步骤1", "步骤2", "步骤3"]}}
  ],
  "resources": [
    {{"title": "资源/工具/活动/人脉", "detail": "用途、限制或跟进方式"}}
  ],
  "open_questions": [
    {{"question": "待确认问题", "context": "为什么重要", "suggested_owner": "建议由谁或哪类人跟进"}}
  ],
  "quotes": [
    {{"quote": "45字以内真实原话", "speaker": "发言人", "meaning": "价值说明"}}
  ],
  "next_month_priorities": ["3-5条下月优先事项"]
}}

合并规则：
- 去重，月报只保留本月最重要的 5-7 个主题。
- 不能把同一件事在摘要、主题、机会、打法里反复讲；每个栏目有清晰分工。
- 只依据分块素材，不新增未经聊天支持的事实。
- 对市场判断、收入机会、课程效果、资源可靠性标明“群友观点/待验证”。
- 不输出完整聊天正文。

分块素材：
{compact}
"""
    return request_json(prompt, max_tokens=5200)


def as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def normalize_content(content: dict[str, object]) -> dict[str, object]:
    defaults: dict[str, object] = {
        "executive_summary": [],
        "month_themes": [],
        "opportunities": [],
        "risks": [],
        "playbook": [],
        "resources": [],
        "open_questions": [],
        "quotes": [],
        "next_month_priorities": [],
    }
    for key, default in defaults.items():
        content[key] = as_list(content.get(key, default))
    return content


def local_fallback_content(
    messages: list[dict[str, object]], stats: dict[str, object], group: str, month: str
) -> dict[str, object]:
    themes = keyword_counts(messages)
    top_themes = themes.most_common(6)
    if not top_themes:
        top_themes = [("综合讨论", stats["message_count"])]
    executive = [
        (
            f"{month} 月报覆盖 {stats['start_time']} 至 {stats['end_time']}，"
            f"共 {stats['message_count']} 条消息、{stats['participant_count']} 位发言者，"
            f"活跃 {stats['active_days']} 天。"
        ),
        "本地回退摘要依据关键词、活跃度和消息类型生成，适合做复盘索引；细粒度观点仍建议结合原始导出核对。",
        "高频主题集中在：" + "、".join(theme for theme, _ in top_themes[:5]) + "。",
        "下月复盘可优先追踪高频主题是否产生实际项目、成交、合作或可复用 SOP。",
    ]
    month_themes = [
        {
            "title": theme,
            "summary": f"本月约 {count} 条消息命中该主题关键词。",
            "evidence": "依据群内消息关键词统计，未进行外部事实补充。",
            "action": "抽取对应日期的高互动讨论，确认是否有可落地项目或资源。",
        }
        for theme, count in top_themes
    ]
    return normalize_content(
        {
            "executive_summary": executive,
            "month_themes": month_themes,
            "opportunities": [
                {
                    "title": "从高频主题中筛选可执行机会",
                    "detail": "优先查看商业模式、AI工具、内容流量、合作资源类讨论。",
                    "next_step": "按主题回看高峰日期，整理联系人、工具、案例和待验证假设。",
                    "confidence": "中：来自本地统计，未做语义深度判断。",
                }
            ],
            "risks": [
                {
                    "title": "关键词统计无法判断事实真伪",
                    "why": "本地回退没有调用大模型做语义归纳，可能遗漏上下文和反讽。",
                    "mitigation": "对金额、效果、承诺、资源可靠性回看原始消息再决策。",
                }
            ],
            "playbook": [
                {
                    "title": "月度复盘三步法",
                    "steps": [
                        "先按高峰日期定位集中讨论。",
                        "再按主题筛出机会、资源和风险。",
                        "最后给每个机会补负责人、验证标准和截止日期。",
                    ],
                }
            ],
            "resources": [],
            "open_questions": [
                {
                    "question": "哪些讨论已经转化为真实合作或收入？",
                    "context": "群内消息量高不等于商业闭环完成。",
                    "suggested_owner": "群主或项目发起人",
                }
            ],
            "quotes": [],
            "next_month_priorities": [
                "把高频机会拆成可验证的小实验。",
                "建立资源清单和跟进状态。",
                "对收入、转化、成本相关说法做事实核验。",
            ],
        }
    )


def first_dict_items(
    summaries: list[dict[str, object]], key: str, limit: int
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for summary in summaries:
        for item in as_list(summary.get(key)):
            if not isinstance(item, dict):
                continue
            title = str(
                item.get("title")
                or item.get("question")
                or item.get("quote")
                or item.get("summary")
                or ""
            ).strip()
            if not title:
                continue
            marker = re.sub(r"\s+", "", title.lower())[:40]
            if marker in seen:
                continue
            seen.add(marker)
            items.append(item)
            if len(items) >= limit:
                return items
    return items


def local_merge_chunk_summaries(
    chunk_summaries: list[dict[str, object]],
    stats: dict[str, object],
    group: str,
    month: str,
) -> dict[str, object]:
    topics = first_dict_items(chunk_summaries, "core_topics", 7)
    opportunities = first_dict_items(chunk_summaries, "business_opportunities", 6)
    risks = first_dict_items(chunk_summaries, "risks", 6)
    actions = first_dict_items(chunk_summaries, "actions", 5)
    resources = first_dict_items(chunk_summaries, "resources", 10)
    questions = first_dict_items(chunk_summaries, "questions", 10)
    quotes = first_dict_items(chunk_summaries, "quotes", 6)
    signals: list[str] = []
    seen_signals: set[str] = set()
    for summary in chunk_summaries:
        for signal in as_list(summary.get("signals")):
            text = str(signal).strip()
            marker = re.sub(r"\s+", "", text.lower())[:40]
            if text and marker not in seen_signals:
                signals.append(text)
                seen_signals.add(marker)
            if len(signals) >= 7:
                break

    if not topics:
        return local_fallback_content([], stats, group, month)

    executive = [
        (
            f"{month} 月报覆盖 {stats['start_time']} 至 {stats['end_time']}，"
            f"共 {stats['message_count']} 条消息、{stats['participant_count']} 位发言者，"
            f"活跃 {stats['active_days']} 天。"
        )
    ]
    executive.extend(signals[:5])
    executive = executive[:7]

    month_themes = []
    for item in topics:
        month_themes.append(
            {
                "title": item.get("title", "主题"),
                "summary": item.get("summary", ""),
                "evidence": item.get("evidence", "来自分块摘要，需回看原始消息核验细节。"),
                "action": item.get("action", "沉淀为可验证动作并跟踪结果。"),
            }
        )

    normalized_opportunities = []
    for item in opportunities:
        normalized_opportunities.append(
            {
                "title": item.get("title", "机会"),
                "detail": item.get("detail", ""),
                "next_step": item.get("next_step", ""),
                "confidence": "中：来自分块摘要，涉及收益和效果需二次核验。",
            }
        )

    normalized_questions = []
    for item in questions:
        normalized_questions.append(
            {
                "question": item.get("question", ""),
                "context": item.get("answer", "分块摘要中识别出的待确认问题。"),
                "suggested_owner": "群主/话题发起人/相关群友",
            }
        )

    return normalize_content(
        {
            "executive_summary": executive,
            "month_themes": month_themes,
            "opportunities": normalized_opportunities,
            "risks": risks,
            "playbook": actions,
            "resources": resources,
            "open_questions": normalized_questions,
            "quotes": quotes,
            "next_month_priorities": [
                "把高频机会拆成可验证的小实验，并记录成本、转化和复盘结论。",
                "对涉及收益、课程效果、资源可靠性的说法进行事实核验。",
                "把群内出现的工具、案例、人脉和活动整理成可维护资源表。",
                "围绕最活跃主题组织一次专题复盘，减少重复讨论。",
            ],
        }
    )


def generate_content(
    messages: list[dict[str, object]],
    stats: dict[str, object],
    group: str,
    month: str,
    use_ai: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    if not use_ai:
        return local_fallback_content(messages, stats, group, month), {"mode": "local"}
    chunk_summaries = []
    usages = []
    try:
        chunks = chunk_messages(messages)
        for index, chunk in enumerate(chunks, 1):
            print(
                f"[INFO] AI chunk {index}/{len(chunks)}: "
                f"{chunk[0]['time']} -> {chunk[-1]['time']}, messages={len(chunk)}"
            )
            summary, metadata = summarize_chunk(chunk, group, month, index, len(chunks))
            chunk_summaries.append(summary)
            usages.append(metadata)
        print("[INFO] AI merge monthly summaries")
        content, metadata = merge_ai_summaries(chunk_summaries, stats, group, month)
        metadata["mode"] = "ai"
        metadata["chunks"] = len(chunks)
        metadata["chunk_usage"] = usages
        return normalize_content(content), metadata
    except Exception as exc:
        if chunk_summaries:
            print(
                "[WARN] AI merge failed, using local merge of chunk summaries: "
                f"{type(exc).__name__}: {exc}"
            )
            content = local_merge_chunk_summaries(chunk_summaries, stats, group, month)
            return content, {
                "mode": "ai_chunks_local_merge",
                "error": f"{type(exc).__name__}: {exc}",
                "chunks": len(chunk_summaries),
                "chunk_usage": usages,
            }
        print(f"[WARN] AI summary failed, falling back to local stats: {type(exc).__name__}: {exc}")
        content = local_fallback_content(messages, stats, group, month)
        return content, {"mode": "local_fallback", "error": f"{type(exc).__name__}: {exc}"}


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.setFont("MicrosoftYaHei", 7)
            self.setFillColor(SLATE)
            self.drawCentredString(A4[0] / 2, 8 * mm, f"{self._pageNumber} / {page_count}")
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


def make_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle(
            "eyebrow",
            parent=base["Normal"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=8,
            leading=10,
            textColor=BLUE,
            alignment=TA_CENTER,
            uppercase=True,
        ),
        "title": ParagraphStyle(
            "title",
            parent=base["Title"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=22,
            leading=28,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=4 * mm,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontName="MicrosoftYaHei",
            fontSize=9,
            leading=14,
            textColor=SLATE,
            alignment=TA_CENTER,
        ),
        "section": ParagraphStyle(
            "section",
            parent=base["Heading1"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=14,
            leading=18,
            textColor=NAVY,
            spaceBefore=7 * mm,
            spaceAfter=3 * mm,
        ),
        "card_title": ParagraphStyle(
            "card_title",
            parent=base["Heading2"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=10,
            leading=13,
            textColor=NAVY,
            spaceAfter=1.5 * mm,
        ),
        "base": ParagraphStyle(
            "base",
            parent=base["Normal"],
            fontName="MicrosoftYaHei",
            fontSize=8.7,
            leading=13.5,
            textColor=NAVY,
            alignment=TA_LEFT,
        ),
        "small": ParagraphStyle(
            "small",
            parent=base["Normal"],
            fontName="MicrosoftYaHei",
            fontSize=7.3,
            leading=10,
            textColor=SLATE,
        ),
        "label": ParagraphStyle(
            "label",
            parent=base["Normal"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=7.5,
            leading=10,
            textColor=SLATE,
        ),
        "stat": ParagraphStyle(
            "stat",
            parent=base["Normal"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=13,
            leading=16,
            textColor=BLUE,
            alignment=TA_CENTER,
        ),
        "stat_label": ParagraphStyle(
            "stat_label",
            parent=base["Normal"],
            fontName="MicrosoftYaHei",
            fontSize=7.2,
            leading=9,
            textColor=SLATE,
            alignment=TA_CENTER,
        ),
    }


def paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(esc(value), style)


def section(title: str, st: dict[str, ParagraphStyle]) -> Paragraph:
    return Paragraph(f'<font color="#3265A8">●</font> {esc(title)}', st["section"])


def card(title: str, body: list[object], st: dict[str, ParagraphStyle], width: float):
    flows = [Paragraph(esc(title), st["card_title"])]
    for item in body:
        if isinstance(item, list):
            for sub in item:
                flows.append(Paragraph(f"· {esc(sub)}", st["base"]))
        else:
            flows.append(Paragraph(esc(item), st["base"]))
    table = Table([[flows]], colWidths=[width])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CARD),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return KeepTogether([table, Spacer(1, 2.5 * mm)])


def stats_grid(stats: dict[str, object], st: dict[str, ParagraphStyle], width: float) -> Table:
    values = [
        (stats["message_count"], "消息数"),
        (stats["participant_count"], "参与人数"),
        (stats["active_days"], "活跃天数"),
        ("、".join(stats["peak_hours"][:3]), "高峰时段"),
    ]
    cells = [
        [Paragraph(str(value), st["stat"]), Paragraph(label, st["stat_label"])]
        for value, label in values
    ]
    table = Table([cells], colWidths=[width / 4] * 4)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CARD),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def simple_table(
    headers: list[str],
    rows: list[list[object]],
    st: dict[str, ParagraphStyle],
    col_widths: list[float],
) -> Table:
    data = [[Paragraph(esc(cell), st["label"]) for cell in headers]]
    data.extend([[Paragraph(esc(cell), st["base"]) for cell in row] for row in rows])
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), TRACK),
                ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def daily_rows(stats: dict[str, object]) -> list[list[object]]:
    daily = stats["daily_counts"]
    max_count = max(daily.values()) if daily else 1
    rows = []
    for date, count in daily.items():
        bar_len = max(1, round(count / max_count * 18))
        rows.append([date, count, "█" * bar_len])
    return rows


def build_pdf(
    output: Path,
    content: dict[str, object],
    stats: dict[str, object],
    group: str,
    month: str,
    metadata: dict[str, object],
) -> None:
    register_fonts()
    output.parent.mkdir(parents=True, exist_ok=True)
    st = make_styles()
    doc = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"微信群聊月报_{group}_{month}",
        author="Codex",
    )
    width = doc.width
    story = [
        Spacer(1, 8 * mm),
        Paragraph("WECHAT MONTHLY REPORT", st["eyebrow"]),
        Paragraph(f"{esc(group)}", st["title"]),
        Paragraph(f"{month} 月报 · {stats['start_time']} 至 {stats['end_time']}", st["subtitle"]),
        Spacer(1, 8 * mm),
        stats_grid(stats, st, width),
        Spacer(1, 5 * mm),
    ]

    story.append(section("核心摘要", st))
    for item in content["executive_summary"][:7]:
        story.append(Paragraph(f"· {esc(item)}", st["base"]))
    story.append(Spacer(1, 2 * mm))

    story.append(section("月度主线", st))
    for item in content["month_themes"][:7]:
        if isinstance(item, dict):
            body = [
                item.get("summary", ""),
                f"依据：{item.get('evidence', '')}",
                f"动作：{item.get('action', '')}",
            ]
            story.append(card(str(item.get("title") or "主题"), body, st, width))
        else:
            story.append(card("主题", [item], st, width))

    story.append(section("机会与可执行打法", st))
    for item in content["opportunities"][:6]:
        if isinstance(item, dict):
            body = [
                item.get("detail", ""),
                f"下一步：{item.get('next_step', '')}",
                f"可信度：{item.get('confidence', '')}",
            ]
            story.append(card(str(item.get("title") or "机会"), body, st, width))
    for item in content["playbook"][:5]:
        if isinstance(item, dict):
            story.append(card(str(item.get("title") or "打法"), [as_list(item.get("steps"))], st, width))

    story.append(section("风险与待验证事项", st))
    for item in content["risks"][:6]:
        if isinstance(item, dict):
            body = [item.get("why", ""), f"规避建议：{item.get('mitigation', '')}"]
            story.append(card(str(item.get("title") or "风险"), body, st, width))

    if content["resources"]:
        story.append(section("资源与线索", st))
        rows = []
        for item in content["resources"][:12]:
            if isinstance(item, dict):
                rows.append([item.get("title", ""), item.get("detail", "")])
            else:
                rows.append(["资源", item])
        story.append(simple_table(["资源", "用途/限制/跟进"], rows, st, [width * 0.32, width * 0.68]))
        story.append(Spacer(1, 3 * mm))

    story.append(section("开放问题", st))
    rows = []
    for item in content["open_questions"][:10]:
        if isinstance(item, dict):
            rows.append(
                [
                    item.get("question", ""),
                    item.get("context", ""),
                    item.get("suggested_owner", ""),
                ]
            )
    if rows:
        story.append(simple_table(["问题", "上下文", "建议跟进"], rows, st, [width * 0.38, width * 0.42, width * 0.20]))
    story.append(Spacer(1, 3 * mm))

    story.append(section("活跃与结构数据", st))
    sender_rows = [
        [index, name, count]
        for index, (name, count) in enumerate(stats["top_senders"][:10], 1)
    ]
    story.append(simple_table(["排名", "发言者", "消息数"], sender_rows, st, [width * 0.12, width * 0.62, width * 0.26]))
    story.append(Spacer(1, 3 * mm))
    type_rows = [[kind, count] for kind, count in stats["message_types"].items()]
    story.append(simple_table(["消息类型", "数量"], type_rows, st, [width * 0.55, width * 0.45]))
    story.append(Spacer(1, 3 * mm))
    story.append(simple_table(["日期", "消息数", "相对热度"], daily_rows(stats), st, [width * 0.24, width * 0.18, width * 0.58]))

    if content["quotes"]:
        story.append(section("代表性原话", st))
        for item in content["quotes"][:6]:
            if isinstance(item, dict):
                story.append(
                    card(
                        f"{item.get('speaker', '群友')}",
                        [f"“{item.get('quote', '')}”", item.get("meaning", "")],
                        st,
                        width,
                    )
                )

    story.append(section("下月优先级", st))
    for item in content["next_month_priorities"][:6]:
        story.append(Paragraph(f"· {esc(item)}", st["base"]))
    story.extend(
        [
            Spacer(1, 5 * mm),
            Paragraph(f"生成方式：{metadata.get('mode', 'unknown')}", st["small"]),
            Paragraph("声明：本报告仅依据本机可访问的群聊记录生成，涉及商业判断、收益和资源可靠性均需二次核验。", st["small"]),
            Paragraph(datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"), st["small"]),
        ]
    )
    doc.build(story, canvasmaker=NumberedCanvas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a WeChat monthly PDF report.")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--input-dir", type=Path, default=Path("outputs") / "chat_exports")
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--group", default="")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--content-json", type=Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--no-ai", action="store_true")
    args = parser.parse_args()

    input_path = args.input or find_latest_jsonl(args.input_dir)
    group = args.group or input_path.stem
    messages = load_month_messages(input_path, args.month)
    stats = build_stats(messages)
    stem = safe_filename(f"{group}_{args.month}_月报")
    content_path = args.content_json or args.out_dir / f"{stem}_内容.json"
    meta_path = args.out_dir / f"{stem}_元数据.json"
    pdf_path = args.out_dir / f"{stem}.pdf"

    if content_path.exists() and not args.refresh:
        content = normalize_content(json.loads(content_path.read_text(encoding="utf-8")))
        metadata = {"mode": "cached"}
        if meta_path.exists():
            metadata.update(json.loads(meta_path.read_text(encoding="utf-8")).get("ai", {}))
        print(f"[INFO] using cached content: {content_path.resolve()}")
    else:
        content, metadata = generate_content(
            messages, stats, group, args.month, use_ai=not args.no_ai
        )
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_text(
            json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] content: {content_path.resolve()}")

    build_pdf(pdf_path, content, stats, group, args.month, metadata)
    meta_path.write_text(
        json.dumps(
            {
                "input": str(input_path.resolve()),
                "group": group,
                "month": args.month,
                "stats": stats,
                "ai": metadata,
                "content": str(content_path.resolve()),
                "pdf": str(pdf_path.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[DONE] month={args.month}, messages={stats['message_count']}, participants={stats['participant_count']}")
    print(f"[OK] PDF: {pdf_path.resolve()}")
    print(f"[OK] metadata: {meta_path.resolve()}")


if __name__ == "__main__":
    main()
