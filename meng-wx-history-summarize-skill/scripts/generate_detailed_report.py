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
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


DEFAULT_OUT_DIR = Path("outputs") / "daily_reports"
CHINA_TZ = timezone(timedelta(hours=8))

# Anthropic-inspired color system - warm, approachable, intelligent
# Based on Claude's brand: warm neutrals with orange/amber accents
NAVY = colors.HexColor("#1F1F1F")        # Primary text - warm dark gray
CLAUDE_ORANGE = colors.HexColor("#D97757") # Primary accent - Claude brand
WARM_ORANGE = colors.HexColor("#E88B6C")  # Lighter accent
PAPER = colors.HexColor("#FFFCF9")       # Warm white background
CARD_BG = colors.HexColor("#FFF8F3")     # Warm card background
BORDER = colors.HexColor("#F0E6DC")      # Warm border
MUTED = colors.HexColor("#6B6766")       # Warm muted text
TRACK = colors.HexColor("#F5EDE4")       # Progress track

# Semantic accent colors
AMBER = colors.HexColor("#D97706")       # Warnings, highlights
EMERALD = colors.HexColor("#059669")     # Positive, consensus
RED = colors.HexColor("#CC5544")         # Alerts (warmer red)


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


def load_messages(path: Path, target_date: str) -> list[dict[str, object]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if str(row.get("time", "")).startswith(target_date + " "):
                rows.append(row)
    if not rows:
        raise RuntimeError(f"no messages found for {target_date}")
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
        elif "微信红包" in str(row.get("content") or ""):
            counts["红包消息"] += 1
        elif "http://" in str(row.get("content") or "") or "https://" in str(
            row.get("content") or ""
        ):
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
    hours = Counter(str(row.get("time") or "")[11:13] for row in messages)
    return {
        "message_count": len(messages),
        "participant_count": len(senders),
        "start_time": messages[0]["time"],
        "end_time": messages[-1]["time"],
        "top_senders": senders.most_common(10),
        "peak_hours": [hour + ":00" for hour, _ in hours.most_common(4)],
        "message_types": classify_messages(messages),
    }


def transcript(messages: list[dict[str, object]]) -> str:
    lines = []
    for row in messages:
        content = re.sub(r"\s+", " ", str(row.get("content") or "")).strip()
        if len(content) > 900:
            content = content[:899] + "…"
        lines.append(
            f"[{str(row.get('time'))[11:16]}] "
            f"{row.get('sender_name') or '系统'} "
            f"({row.get('message_kind') or '其他'}): {content}"
        )
    return "\n".join(lines)


def resolve_api() -> tuple[str, str]:
    token = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get(
        "ANTHROPIC_AUTH_TOKEN"
    )
    if not token:
        raise RuntimeError("DeepSeek API credential is not configured")
    base = (
        os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL")
        or "https://api.deepseek.com"
    ).rstrip("/")
    if base.endswith("/anthropic"):
        base = base[: -len("/anthropic")]
    return base + "/chat/completions", token


def generate_content(
    messages: list[dict[str, object]], stats: dict[str, object], group: str, date: str
) -> tuple[dict[str, object], dict[str, object]]:
    prompt = f"""请依据以下微信群聊记录，为商业社群生成“详细版群聊日报”的结构化 JSON。
禁止补充聊天中没有的事实。涉及数据、法规、市场判断时必须注明是群友观点或待验证信息。

群聊：{group}
日期：{date}
消息数：{stats['message_count']}
参与者：{stats['participant_count']}

JSON 必须严格符合下面结构，不要输出 Markdown 代码块：
{{
  "executive_summary": ["5-6条，每条50-100字，只写当天最重要结论，不展开细节"],
  "actionable_tips": [
    {{"title": "行动或SOP标题", "steps": ["2-3条可执行步骤"]}}
  ],
  "hot_topics": [
    {{
      "title": "热点标题",
      "core": "只写该话题最核心的一句话",
      "supplement": "只补充摘要里没有写过的讨论脉络",
      "participants": "相关群友，使用聊天中的显示名",
      "consensus": "群内共识；无共识时明确写存在分歧",
      "action": "可执行建议"
    }}
  ],
  "resources": [
    {{"title": "资源或机会名称", "detail": "来源、用途、限制或后续动作"}}
  ],
  "quotes": [
    {{"quote": "不超过45字的原话", "speaker": "发言人", "meaning": "价值说明"}}
  ],
  "qa_groups": [
    {{
      "category": "问题分类",
      "items": [{{"question": "问题", "answer": "基于群聊的回答"}}]
    }}
  ],
  "topic_heat": [
    {{"topic": "话题名", "percent": 25}}
  ],
  "final_summary": "80-140字，只列3项优先下一步，不复述上文已经讲过的事实"
}}

栏目分工和去重要求：
- executive_summary 是目录式结论，不要重复 hot_topics 的完整表述。
- hot_topics 是唯一主叙事区，同一个话题只在这里讲完整一次。
- actionable_tips 只写能立刻执行的 SOP，不把每个热点改写成教程。
- resources 只放真实资源、联系人、货盘、工具、链接或机会，不重复市场判断。
- qa_groups 只放聊天中真实出现的问题或仍需确认的问题，不重复热点结论。
- final_summary 只写下一步优先级，不再复述所有话题和统计数据。

数量要求：
- actionable_tips 3-4 项，每项 2-3 步。
- hot_topics 4-5 项。
- resources 3-5 项。
- quotes 4-6 项，只能使用记录中的真实原话。
- qa_groups 2-3 组，每组 1-2 个问答。
- topic_heat 4-6 项，percent 必须为整数且总和为 100。

聊天记录：
{transcript(messages)}
"""
    endpoint, token = resolve_api()
    payload = {
        "model": os.environ.get("WECHAT_SUMMARY_MODEL", "deepseek-chat"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是严谨的商业社群日报编辑。你只总结提供的记录，"
                    "明确区分事实、群友观点、分歧和待验证信息。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.15,
        "max_tokens": 5000,
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
    content = str(result["choices"][0]["message"]["content"]).strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    return json.loads(content), {
        "model": result.get("model"),
        "usage": result.get("usage", {}),
    }


def normalize_content(content: dict[str, object]) -> dict[str, object]:
    required_lists = [
        "executive_summary",
        "actionable_tips",
        "hot_topics",
        "resources",
        "quotes",
        "qa_groups",
        "topic_heat",
    ]
    for key in required_lists:
        if not isinstance(content.get(key), list):
            content[key] = []
    content["final_summary"] = str(content.get("final_summary") or "")

    def clipped(value: object, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) > limit:
            return text[: limit - 1] + "…"
        return text

    tips = []
    for item in content["actionable_tips"][:4]:
        steps = [clipped(step, 90) for step in item.get("steps", [])[:3]]
        tips.append({"title": clipped(item.get("title"), 32), "steps": steps})
    content["actionable_tips"] = tips

    topics = []
    for item in content["hot_topics"][:5]:
        topics.append(
            {
                "title": clipped(item.get("title"), 34),
                "core": clipped(item.get("core"), 95),
                "supplement": clipped(item.get("supplement"), 120),
                "participants": clipped(item.get("participants"), 75),
                "consensus": clipped(item.get("consensus"), 75),
                "action": clipped(item.get("action"), 75),
            }
        )
    content["hot_topics"] = topics

    resources = [
        {
            "title": clipped(item.get("title"), 36),
            "detail": clipped(item.get("detail"), 120),
        }
        for item in content["resources"][:5]
    ]
    content["resources"] = resources

    topic_titles = [str(item.get("title") or "") for item in topics if item.get("title")]
    resource_titles = [
        str(item.get("title") or "") for item in resources if item.get("title")
    ]
    risk_titles = [
        title
        for title in topic_titles
        if any(word in title for word in ("合规", "风险", "资质", "监管", "农药", "杀虫"))
    ]
    overview = []
    if topic_titles:
        overview.append(f"今日主线：{'、'.join(topic_titles[:4])}。")
    if risk_titles:
        overview.append(f"风险提醒：{'、'.join(risk_titles[:2])}需先验证成本、资质和平台限制。")
    if resource_titles:
        overview.append(f"可直接跟进：{'、'.join(resource_titles[:3])}。")
    content["executive_summary"] = [clipped(item, 110) for item in overview[:4]]

    content["quotes"] = [
        {
            "quote": clipped(item.get("quote"), 48),
            "speaker": clipped(item.get("speaker"), 32),
            "meaning": clipped(item.get("meaning"), 70),
        }
        for item in content["quotes"][:6]
    ]

    def grams(value: object) -> set[str]:
        text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(value or ""))
        return {text[index : index + 4] for index in range(max(0, len(text) - 3))}

    topic_grams = [
        grams(
            " ".join(
                [
                    str(item.get("core") or ""),
                    str(item.get("consensus") or ""),
                    str(item.get("action") or ""),
                ]
            )
        )
        for item in topics
    ]
    qa_groups = []
    for group_item in content["qa_groups"][:3]:
        items = []
        for qa in group_item.get("items", [])[:2]:
            candidate = {
                "question": clipped(qa.get("question"), 70),
                "answer": clipped(qa.get("answer"), 105),
            }
            candidate_grams = grams(candidate["answer"])
            if any(
                candidate_grams
                and len(candidate_grams & known) / max(1, len(candidate_grams | known))
                > 0.28
                for known in topic_grams
            ):
                continue
            items.append(
                {
                    "question": candidate["question"],
                    "answer": candidate["answer"],
                }
            )
        if items:
            qa_groups.append(
                {"category": clipped(group_item.get("category"), 32), "items": items}
            )
    content["qa_groups"] = qa_groups
    content["topic_heat"] = content["topic_heat"][:6]
    content["final_summary"] = clipped(content["final_summary"], 160)

    heat = content["topic_heat"]
    total = sum(max(0, int(item.get("percent", 0))) for item in heat)
    if heat and total != 100:
        running = 0
        for item in heat[:-1]:
            value = round(max(0, int(item.get("percent", 0))) * 100 / max(total, 1))
            item["percent"] = value
            running += value
        heat[-1]["percent"] = max(0, 100 - running)
    return content


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.setFillColor(NAVY)
            self.setFont("MicrosoftYaHei", 6.8)
            self.drawCentredString(A4[0] / 2, 8 * mm, f"第 {self._pageNumber} / {page_count} 页")
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


class HeatBar(Flowable):
    def __init__(self, label: str, percent: int, width: float):
        super().__init__()
        self.label = label
        self.percent = max(0, min(100, percent))
        self.width = width
        self.height = 10 * mm

    def wrap(self, avail_width, avail_height):
        self.width = min(self.width, avail_width)
        return self.width, self.height

    def draw(self):
        label_width = 45 * mm
        bar_x = label_width
        bar_y = 4.5 * mm
        bar_width = self.width - label_width - 14 * mm
        self.canv.setFillColor(NAVY)
        self.canv.setFont("MicrosoftYaHei", 8.5)
        self.canv.drawString(0, 3.5 * mm, self.label[:24])
        self.canv.setFillColor(TRACK)
        self.canv.roundRect(bar_x, bar_y, bar_width, 3.5 * mm, 1.75 * mm, fill=1, stroke=0)
        self.canv.setFillColor(CLAUDE_ORANGE)
        self.canv.roundRect(
            bar_x,
            bar_y,
            bar_width * self.percent / 100,
            3.5 * mm,
            1.75 * mm,
            fill=1,
            stroke=0,
        )
        self.canv.setFillColor(NAVY)
        self.canv.setFont("MicrosoftYaHei-Bold", 8.5)
        self.canv.drawRightString(self.width, 3.4 * mm, f"{self.percent}%")


def styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    base = ParagraphStyle(
        "BaseCN",
        parent=sample["BodyText"],
        fontName="MicrosoftYaHei",
        fontSize=9,
        leading=14.5,
        textColor=NAVY,
        wordWrap="CJK",
        spaceAfter=2.5 * mm,
    )
    return {
        "base": base,
        "eyebrow": ParagraphStyle(
            "Eyebrow",
            parent=base,
            fontName="MicrosoftYaHei",
            fontSize=8,
            leading=11,
            textColor=MUTED,
            spaceAfter=1.5 * mm,
            letterSpacing=0.8,
        ),
        "title": ParagraphStyle(
            "Title",
            parent=base,
            fontName="MicrosoftYaHei-Bold",
            fontSize=20,
            leading=26,
            textColor=NAVY,
            spaceAfter=1.5 * mm,
        ),
        "disclaimer": ParagraphStyle(
            "Disclaimer",
            parent=base,
            fontName="MicrosoftYaHei",
            fontSize=8,
            leading=12,
            textColor=RED,
            spaceAfter=5 * mm,
        ),
        "section": ParagraphStyle(
            "Section",
            parent=base,
            fontName="MicrosoftYaHei-Bold",
            fontSize=13,
            leading=18,
            textColor=NAVY,
            spaceBefore=8 * mm,
            spaceAfter=3 * mm,
        ),
        "card_title": ParagraphStyle(
            "CardTitle",
            parent=base,
            fontName="MicrosoftYaHei-Bold",
            fontSize=9.5,
            leading=14,
            textColor=NAVY,
            spaceAfter=2 * mm,
        ),
        "label": ParagraphStyle(
            "Label",
            parent=base,
            fontName="MicrosoftYaHei-Bold",
            fontSize=8,
            leading=12,
            textColor=CLAUDE_ORANGE,
            spaceAfter=1.5 * mm,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base,
            fontSize=7.5,
            leading=10.5,
            textColor=MUTED,
            spaceAfter=0,
        ),
        "stat_value": ParagraphStyle(
            "StatValue",
            parent=base,
            fontName="MicrosoftYaHei-Bold",
            fontSize=12,
            leading=16,
            textColor=CLAUDE_ORANGE,
            spaceAfter=1 * mm,
        ),
        "stat_label": ParagraphStyle(
            "StatLabel",
            parent=base,
            fontSize=7.5,
            leading=10,
            textColor=MUTED,
            spaceAfter=0,
        ),
        "qa_q": ParagraphStyle(
            "QAQ",
            parent=base,
            fontName="MicrosoftYaHei-Bold",
            fontSize=9,
            leading=13.5,
            textColor=RED,
        ),
        "qa_a": ParagraphStyle(
            "QAA",
            parent=base,
            fontSize=9,
            leading=14,
            textColor=NAVY,
            spaceAfter=0,
        ),
    }


def section_title(number: int, title: str, english: str, st: dict) -> list[Flowable]:
    return [
        Spacer(1, 2 * mm),
        Paragraph(
            f'{number}. {esc(title)}'
            + (f' <font color="{colors.HexColor("#9CA3AF").hexval()}" size="9">/ {esc(english)}</font>' if english else ""),
            st["section"],
        ),
        Spacer(1, 3 * mm),
    ]


def card(flowables: list[Flowable], width: float, background=None) -> Table:
    if background is None:
        background = CARD_BG
    return Table(
        [[flowables]],
        colWidths=[width],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        ),
        cornerRadii=[6, 6, 6, 6],
    )


def bullet(text: object, st: dict, color: str = None) -> Paragraph:
    if color is None:
        color = AMBER.hexval()
    return Paragraph(
        f'<font color="{color}">•</font> {esc(text)}', st["base"]
    )


def build_pdf(
    output: Path,
    content: dict[str, object],
    stats: dict[str, object],
    group: str,
    date: str,
) -> None:
    register_fonts()
    st = styles()
    page_width, _ = A4
    usable_width = page_width - 28 * mm
    doc = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=16 * mm,
        bottomMargin=15 * mm,
        title=f"群聊日报_{group}_{date}_详细版",
        author="AI Assistant",
    )
    story: list[Flowable] = []

    story.append(Paragraph("Group Daily Report", st["eyebrow"]))
    story.append(Paragraph(f"{esc(group)} · {date}", st["title"]))
    story.append(
        Paragraph("声明：本报告由 AI 自动生成，内容仅供参考", st["disclaimer"])
    )

    stats_cells = [
        [
            Paragraph(f"{stats['message_count']} 条", st["stat_value"]),
            Paragraph("消息总数", st["stat_label"]),
        ],
        [
            Paragraph(f"约 {stats['participant_count']} 人", st["stat_value"]),
            Paragraph("有效发言成员", st["stat_label"]),
        ],
        [
            Paragraph(
                f"{str(stats['start_time'])[:16]}<br/>至 {str(stats['end_time'])[:16]}",
                st["stat_value"],
            ),
            Paragraph("日期范围", st["stat_label"]),
        ],
        [
            Paragraph(" / ".join(stats["peak_hours"]), st["stat_value"]),
            Paragraph("高峰时段", st["stat_label"]),
        ],
    ]
    stats_table = Table(
        [[cell for cell in stats_cells]],
        colWidths=[usable_width / 4] * 4,
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CARD_BG),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        ),
        cornerRadii=[6, 6, 6, 6],
    )
    story.extend([stats_table, Spacer(1, 2.5 * mm)])

    type_counts: Counter[str] = stats["message_types"]
    chips = []
    for label, count in type_counts.items():
        chips.append(
            Table(
                [[Paragraph(f"{esc(label)}：{count}", st["small"])]],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF3FC")),
                        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                ),
                cornerRadii=[5, 5, 5, 5],
            )
        )
    chip_rows = [chips[index : index + 5] for index in range(0, len(chips), 5)]
    for row in chip_rows:
        while len(row) < 5:
            row.append("")
    story.extend(
        [
            Table(
                chip_rows,
                colWidths=[usable_width / 5] * 5,
                style=TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                        ("TOPPADDING", (0, 0), (-1, -1), 1),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ]
                ),
            ),
            Spacer(1, 4 * mm),
        ]
    )

    story.extend(section_title(1, "核心摘要", "Executive Summary", st))
    for item in content["executive_summary"]:
        story.append(bullet(item, st))

    story.append(Spacer(1, 3 * mm))
    story.extend(section_title(2, "即学即用技巧库", "Actionable Tips", st))
    for index, item in enumerate(content["actionable_tips"], 1):
        flows = [
            Paragraph(
                f'<font color="#F6B90C">●</font>&nbsp; {index}. {esc(item.get("title"))}',
                st["card_title"],
            )
        ]
        for step in item.get("steps", []):
            flows.append(Paragraph(f"·&nbsp; {esc(step)}", st["base"]))
        story.extend([card(flows, usable_width), Spacer(1, 2.5 * mm)])

    story.append(Spacer(1, 3 * mm))
    story.extend(section_title(3, "今日热点话题", "Today's Hot Topics", st))
    for index, item in enumerate(content["hot_topics"], 1):
        inner = [
            Paragraph(
                f'<font color="#FF3B6B">●</font>&nbsp; {index}. {esc(item.get("title"))}',
                st["card_title"],
            )
        ]
        fields = [
            ("核心观点", item.get("core")),
            ("补充观点", item.get("supplement")),
            ("相关群友/讨论来源", item.get("participants")),
            ("最终共识", item.get("consensus")),
            ("可执行建议", item.get("action")),
        ]
        for label, value in fields:
            label_color = EMERALD.hexval() if label == "最终共识" else CLAUDE_ORANGE.hexval()
            inner.append(
                Paragraph(
                    f'<font color="{label_color}">· {esc(label)}：</font>{esc(value)}',
                    st["base"],
                )
            )
        topic_table = Table(
            [["", inner]],
            colWidths=[1.2 * mm, usable_width - 1.2 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), CLAUDE_ORANGE),
                    ("LEFTPADDING", (0, 0), (0, -1), 0),
                    ("RIGHTPADDING", (0, 0), (0, -1), 0),
                    ("TOPPADDING", (0, 0), (0, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (0, -1), 0),
                    ("LEFTPADDING", (1, 0), (1, -1), 6),
                    ("RIGHTPADDING", (1, 0), (1, -1), 0),
                    ("TOPPADDING", (1, 0), (1, -1), 3),
                    ("BOTTOMPADDING", (1, 0), (1, -1), 3),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            ),
        )
        story.extend([topic_table, Spacer(1, 3 * mm)])

    story.extend(section_title(4, "实用教程与资源分享", "Resources", st))
    for item in content["resources"]:
        story.append(
            KeepTogether(
                [
                    Paragraph(
                        f'<font color="#2764E8">◆</font>&nbsp; {esc(item.get("title"))}',
                        st["card_title"],
                    ),
                    Paragraph(esc(item.get("detail")), st["base"]),
                ]
            )
        )

    story.extend(section_title(5, "有趣对话与金句", "Golden Sentences", st))
    for item in content["quotes"]:
        quote = Paragraph(
            f'“{esc(item.get("quote"))}”<br/>'
            f'<font color="#65758B">{esc(item.get("speaker"))}：{esc(item.get("meaning"))}</font>',
            st["base"],
        )
        story.extend([card([quote], usable_width, colors.white), Spacer(1, 1.8 * mm)])

    if content["qa_groups"]:
        story.extend(section_title(6, "问题与解答", "QA", st))
        for group_item in content["qa_groups"]:
            story.append(
                Paragraph(
                    f'<font color="#FF3B6B">?</font>&nbsp; {esc(group_item.get("category"))}',
                    st["card_title"],
                )
            )
            for qa in group_item.get("items", []):
                flows = [
                    Paragraph(f"Q：{esc(qa.get('question'))}", st["qa_q"]),
                    Spacer(1, 1.5 * mm),
                    Paragraph(f"A：{esc(qa.get('answer'))}", st["qa_a"]),
                ]
                story.extend([card(flows, usable_width, colors.white), Spacer(1, 2 * mm)])

    story.extend(section_title(7, "活跃老铁前5名", "", st))
    top_rows = [
        [
            Paragraph("排名", st["label"]),
            Paragraph("群友", st["label"]),
            Paragraph("消息数", st["label"]),
            Paragraph("主要贡献", st["label"]),
        ]
    ]
    contribution_by_member = {}
    for topic in content["hot_topics"]:
        for name in str(topic.get("participants") or "").split("、"):
            name = name.strip()
            if name:
                contribution_by_member.setdefault(name, []).append(
                    str(topic.get("title") or "")
                )
    for rank, (name, count) in enumerate(stats["top_senders"][:5], 1):
        related = list(contribution_by_member.get(name, []))
        if not related:
            for alias, titles in contribution_by_member.items():
                if len(alias) >= 2 and (alias in name or name in alias):
                    related.extend(titles)
        contribution = "；".join(related[:3]) or "参与当天核心讨论并持续输出观点。"
        top_rows.append(
            [
                Paragraph(str(rank), st["base"]),
                Paragraph(esc(name), st["base"]),
                Paragraph(str(count), st["base"]),
                Paragraph(esc(contribution), st["base"]),
            ]
        )
    story.append(
        Table(
            top_rows,
            colWidths=[12 * mm, 34 * mm, 18 * mm, usable_width - 64 * mm],
            repeatRows=1,
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF2FF")),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            ),
        )
    )

    story.extend(section_title(8, "话题热度分布", "", st))
    for item in content["topic_heat"]:
        story.append(
            HeatBar(
                str(item.get("topic") or ""),
                int(item.get("percent") or 0),
                usable_width,
            )
        )

    story.append(
        KeepTogether(
            section_title(9, "总结", "", st)
            + [bullet(content["final_summary"], st)]
        )
    )
    story.extend(
        [
            Spacer(1, 4 * mm),
            Table(
                [[""]],
                colWidths=[usable_width],
                rowHeights=[0.35 * mm],
                style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), BORDER)]),
            ),
            Spacer(1, 2.5 * mm),
            Paragraph("制作信息：版权归群主所有", st["small"]),
            Paragraph("声明：所有内容由AI总结而成，不保证准确度！请注意甄别！", st["small"]),
            Paragraph(datetime.now().strftime("%Y/%m/%d"), st["small"]),
        ]
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    doc.build(story, canvasmaker=NumberedCanvas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a detailed WeChat daily PDF.")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--date")
    parser.add_argument("--group")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--content-json", type=Path)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    input_path = args.input or find_latest_jsonl(Path("outputs") / "chat_exports")
    target_date = args.date or str(
        datetime.now(CHINA_TZ).date() - timedelta(days=1)
    )
    group_name = args.group or input_path.stem
    messages = load_messages(input_path, target_date)
    stats = build_stats(messages)
    content_path = args.content_json or args.out_dir / (
        f"{target_date}_{group_name}_详细版内容.json"
    )
    meta_path = args.out_dir / f"{target_date}_{group_name}_详细版元数据.json"

    metadata = {}
    if content_path.exists() and not args.refresh:
        content = json.loads(content_path.read_text(encoding="utf-8"))
        if meta_path.exists():
            metadata = json.loads(meta_path.read_text(encoding="utf-8")).get("ai", {})
        print(f"[INFO] using cached content: {content_path.resolve()}")
    else:
        content, metadata = generate_content(
            messages, stats, group_name, target_date
        )
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_text(
            json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] generated content: {content_path.resolve()}")

    content = normalize_content(content)
    content_path.parent.mkdir(parents=True, exist_ok=True)
    content_path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output = args.out_dir / f"群聊日报_{group_name}_{target_date}_详细版.pdf"
    build_pdf(output, content, stats, group_name, target_date)

    meta_path.write_text(
        json.dumps(
            {
                "input": str(input_path.resolve()),
                "date": target_date,
                "group": group_name,
                "stats": {
                    **stats,
                    "message_types": dict(stats["message_types"]),
                },
                "ai": metadata,
                "content": str(content_path.resolve()),
                "pdf": str(output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[DONE] PDF: {output.resolve()}")
    print(f"[OK] metadata: {meta_path.resolve()}")


if __name__ == "__main__":
    main()
