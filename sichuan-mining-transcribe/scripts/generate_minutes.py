from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any


KEY_DATA_KEYWORDS = {
    "品位": ["品位", "零点", "0.", "1.", "5以上"],
    "回收率": ["回收率", "收率", "回收"],
    "吨数/产量": ["吨", "万吨", "千吨", "两千", "五百"],
    "报价/价格": ["报价", "价格", "含税", "不含税", "一千", "1000", "2600", "2300"],
    "付款": ["付款", "预付", "定金", "打款"],
    "运输": ["运费", "运输", "装车", "自提", "公里"],
    "加工": ["加工费", "代加工", "选矿", "洗选"],
    "合规": ["资质", "采矿证", "初设", "安设", "环评", "挂靠"],
}

ACTION_KEYWORDS = [
    "报价",
    "报个价",
    "算一下",
    "联系",
    "确认",
    "确定",
    "提供",
    "发给",
    "沟通",
    "安排",
    "提交",
    "复核",
    "优化",
]

RISK_KEYWORDS = [
    "资质",
    "挂靠",
    "合同",
    "付款",
    "套保",
    "品位",
    "回收率",
    "尾矿",
    "运费",
    "初设",
    "安设",
    "环评",
    "不确定",
    "不清楚",
]

NUMBER_PATTERN = re.compile(
    r"\d+(?:\.\d+)?%?|[零一二三四五六七八九十百千万两]+(?:点[零一二三四五六七八九十])?"
)


def extract_source_names(text: str) -> list[str]:
    names = re.findall(r"^# Source:\s*(.+)$", text, flags=re.MULTILINE)
    return names or ["未命名转写源"]


def iter_lines_with_source(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_source = "未命名转写源"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# Source:"):
            current_source = line.replace("# Source:", "", 1).strip()
            continue
        if line.startswith("# Path:") or line.startswith("# Kind:") or line.startswith("--- PAGE"):
            continue
        if re.match(r"^\d{2}-\d{2}\s+", line):
            continue
        rows.append({"source": current_source, "line": line})
    return rows


def classify_line(line: str, mapping: dict[str, list[str]]) -> str | None:
    for label, keywords in mapping.items():
        if any(keyword in line for keyword in keywords):
            return label
    return None


def extract_key_data(text: str, limit: int = 80) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in iter_lines_with_source(text):
        line = item["line"]
        label = classify_line(line, KEY_DATA_KEYWORDS)
        if not label:
            continue
        numbers = NUMBER_PATTERN.findall(line)
        if label != "合规" and not has_data_signal(line):
            continue
        rows.append(
            {
                "source": item["source"],
                "type": label,
                "value": "、".join(numbers[:8]),
                "context": shorten(line, 180),
                "needs_review": "是",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def extract_action_items(text: str, limit: int = 30) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in iter_lines_with_source(text):
        line = item["line"]
        if re.match(r"^\d{2}-\d{2}\s+", line):
            continue
        if not any(keyword in line for keyword in ACTION_KEYWORDS):
            continue
        rows.append(
            {
                "source": item["source"],
                "item": shorten(line, 160),
                "owner": extract_owner(line),
                "due": extract_due(line),
                "deliverable": infer_deliverable(line),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def extract_uncertain_items(
    text: str,
    rule_changes: list[dict[str, Any]] | None = None,
    llm_uncertain: list[dict[str, Any]] | None = None,
    limit: int = 120,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in iter_lines_with_source(text):
        line = item["line"]
        if is_uncertain_line(line):
            rows.append(
                {
                    "source": item["source"],
                    "text": shorten(line, 180),
                    "reason": "含不确定表达、异常字符或可能误识别内容",
                }
            )
        if len(rows) >= limit:
            break

    for change in rule_changes or []:
        if str(change.get("status", "")) == "candidate":
            rows.append(
                {
                    "source": str(change.get("rule_type", "rule")),
                    "text": f"{change.get('source')} -> {change.get('target')}",
                    "reason": str(change.get("note", "候选规则需复核")),
                }
            )

    for item in llm_uncertain or []:
        rows.append(
            {
                "source": "llm",
                "text": str(item.get("text", "")),
                "reason": str(item.get("reason", "模型标记不确定")),
            }
        )

    return rows[:limit]


def generate_minutes(
    corrected_text: str,
    key_data: list[dict[str, str]],
    action_items: list[dict[str, str]],
    uncertain_items: list[dict[str, str]],
    template: str = "business",
    semantic: dict[str, Any] | None = None,
    source_names_override: list[str] | None = None,
) -> str:
    source_names = source_names_override or extract_source_names(corrected_text)
    conclusions = as_str_list((semantic or {}).get("conclusions")) or infer_conclusions(corrected_text)
    topics = as_dict_list((semantic or {}).get("topics")) or build_topics(corrected_text)
    technical = as_str_list((semantic or {}).get("technical_points")) or build_technical_points(corrected_text)
    risks = as_dict_list((semantic or {}).get("risks")) or build_risk_rows(corrected_text, uncertain_items)

    return "\n".join(
        [
            "# 会议纪要",
            "",
            "## 一、会议信息",
            f"- 会议主题：{infer_subject(source_names)}",
            "- 会议时间：以原始文件名和转写时间戳为准",
            f"- 会议类型：{infer_meeting_type(source_names, corrected_text)}",
            f"- 原始文件：{', '.join(source_names)}",
            f"- 输出模板：{template}",
            "",
            "## 二、会议结论",
            format_bullets(conclusions),
            "",
            "## 三、关键议题摘要",
            format_topics(topics),
            "",
            "## 四、关键数据与商务条件",
            format_table(key_data[:18], ["type", "value", "context", "needs_review"], ["类型", "数值/条件", "说明/上下文", "是否需复核"]),
            "",
            "## 五、技术与生产要点",
            format_bullets(technical),
            "",
            "## 六、风险与待确认事项",
            format_table(risks[:12], ["risk", "impact", "suggestion"], ["风险/疑点", "影响", "建议确认方式"]),
            "",
            "## 七、行动项",
            format_table(action_items[:12], ["item", "owner", "due", "deliverable"], ["事项", "负责人", "截止时间", "产出物"]),
            "",
            "## 八、转写纠错疑点",
            format_table(uncertain_items[:20], ["text", "reason", "source"], ["疑点", "原因", "来源"]),
            "",
        ]
    )


def write_key_data_csv(path: Path, rows: list[dict[str, str]]) -> None:
    write_csv(path, rows, ["source", "type", "value", "context", "needs_review"])


def write_action_items_csv(path: Path, rows: list[dict[str, str]]) -> None:
    write_csv(path, rows, ["source", "item", "owner", "due", "deliverable"])


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_uncertain_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text(
        "\n".join(
            [
                "# 转写纠错疑点",
                "",
                format_table(rows, ["text", "reason", "source"], ["疑点", "原因", "来源"]),
                "",
            ]
        ),
        encoding="utf-8",
    )


def infer_conclusions(text: str) -> list[str]:
    conclusions: list[str] = []
    if contains_any(text, ["报价", "价格", "付款", "预付", "含税"]):
        conclusions.append("会议涉及矿石采购、报价、付款方式及运输费用等商务条件，关键数字需结合正式报价单复核。")
    if contains_any(text, ["品位", "回收率", "精粉", "原矿"]):
        conclusions.append("讨论重点包括原矿品位、精粉目标品位和回收率，样品代表性和检测数据是后续决策基础。")
    if contains_any(text, ["选矿", "洗选", "浮选", "尾矿"]):
        conclusions.append("选矿工艺、尾矿处理和加工能力是合作可行性的核心技术问题。")
    if contains_any(text, ["采矿", "资质", "挂靠", "初设", "安设"]):
        conclusions.append("采矿合作需关注资质、初设/安设进度、施工图和合规责任边界。")
    if contains_any(text, ["运费", "装车", "自提", "堆场"]):
        conclusions.append("运输距离、装车条件、堆场组织和当地协调会直接影响到矿石落地成本。")
    if not conclusions:
        conclusions.append("本次转写已完成初步纠错，建议结合疑点清单复核关键事实后再形成正式对外版本。")
    return conclusions


def build_topics(text: str) -> list[dict[str, str]]:
    topic_defs = [
        ("商务报价与付款", ["报价", "价格", "含税", "付款", "预付", "定金"]),
        ("品位、回收率与样品", ["品位", "回收率", "样", "检测", "实验"]),
        ("采矿与合规", ["采矿", "资质", "挂靠", "初设", "安设", "施工图"]),
        ("选矿、加工与尾矿", ["选矿", "加工", "浮选", "洗选", "尾矿", "精粉"]),
        ("运输与现场组织", ["运费", "运输", "装车", "自提", "堆场", "公里"]),
    ]
    rows = iter_lines_with_source(text)
    topics: list[dict[str, str]] = []
    for title, keywords in topic_defs:
        snippets = [shorten(row["line"], 140) for row in rows if contains_any(row["line"], keywords)]
        if not snippets:
            continue
        topics.append(
            {
                "title": title,
                "points": "；".join(snippets[:4]),
                "judgement": "需结合正式数据和责任人确认。",
                "open": "详见关键数据、行动项和疑点清单。",
            }
        )
    return topics


def build_technical_points(text: str) -> list[str]:
    points: list[str] = []
    for title, keywords in [
        ("原矿/精粉品位", ["原矿", "精粉", "品位"]),
        ("回收率", ["回收率", "收率"]),
        ("选矿工艺", ["选矿", "浮选", "洗选", "破碎"]),
        ("尾矿处理", ["尾矿", "钾长石", "砂石"]),
        ("运输与堆场", ["运费", "运输", "装车", "堆场"]),
        ("产能约束", ["产能", "日处理", "两千吨", "万吨"]),
    ]:
        snippets = [row["line"] for row in iter_lines_with_source(text) if contains_any(row["line"], keywords)]
        if snippets:
            points.append(f"{title}：{shorten('；'.join(snippets[:2]), 180)}")
        else:
            points.append(f"{title}：未在本地规则摘要中明确提取，建议人工复核。")
    return points


def build_risk_rows(text: str, uncertain_items: list[dict[str, str]]) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    if contains_any(text, ["资质", "挂靠", "采矿证"]):
        risks.append({"risk": "采矿资质或挂靠安排不清", "impact": "可能影响合作合规性", "suggestion": "复核资质文件和合同责任边界"})
    if contains_any(text, ["品位", "回收率", "样"]):
        risks.append({"risk": "样品代表性和品位/回收率需确认", "impact": "可能影响报价和加工收益", "suggestion": "重新取综合样并保留检测报告"})
    if contains_any(text, ["付款", "预付", "合同"]):
        risks.append({"risk": "付款方式和合同条款需落书面", "impact": "可能形成资金和履约风险", "suggestion": "以正式报价单和合同文本为准"})
    if contains_any(text, ["运费", "运输", "装车"]):
        risks.append({"risk": "运输成本和现场装车条件需核算", "impact": "可能压缩加工或采购利润", "suggestion": "按线路、车型、装卸条件单独测算"})
    for item in uncertain_items[:8]:
        risks.append({"risk": item.get("text", ""), "impact": "转写不确定，可能影响理解", "suggestion": item.get("reason", "人工复核")})
    return risks or [{"risk": "暂无自动识别风险", "impact": "仍需人工复核关键事实", "suggestion": "检查纠错全文和原始转写"}]


def format_topics(topics: list[dict[str, str]]) -> str:
    if not topics:
        return "暂无自动提取议题，建议人工复核纠错全文。"
    parts: list[str] = []
    for index, topic in enumerate(topics, start=1):
        parts.extend(
            [
                f"### 议题 {index}：{topic.get('title', '未命名议题')}",
                f"- 背景/讨论要点：{topic.get('points', topic.get('summary', '待补充'))}",
                f"- 初步判断：{topic.get('judgement', '需结合正式数据和责任人确认。')}",
                f"- 未决问题：{topic.get('open', topic.get('open_questions', '详见关键数据、行动项和疑点清单。'))}",
                "",
            ]
        )
    return "\n".join(parts).strip()


def format_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- 暂无"


def format_table(rows: list[dict[str, str]], keys: list[str], headers: list[str]) -> str:
    if not rows:
        return "| " + " | ".join(headers) + " |\n|" + "|".join(["---"] * len(headers)) + "|\n"
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        values = [escape_cell(str(row.get(key, ""))) for key in keys]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def infer_subject(source_names: list[str]) -> str:
    titles = [Path(name).stem for name in source_names]
    if len(titles) == 1:
        return titles[0]
    return "；".join(titles[:4])


def infer_meeting_type(source_names: list[str], text: str) -> str:
    joined = " ".join(source_names) + " " + text[:1000]
    if "客户" in joined:
        return "客户会议 / 商务与技术交流"
    if "内部" in joined:
        return "内部会议 / 经营与技术分析"
    return "矿业会议 / 转写后整理"


def extract_owner(line: str) -> str:
    known = ["曹总", "刘总", "彭总", "杨总", "徐总", "谢总", "梁总", "南总", "罗总", "张主任", "兰总"]
    for name in known:
        if name in line:
            return name
    match = re.search(r"[\u4e00-\u9fff]{1,3}(?:总|主任|经理|老板)", line)
    return match.group(0) if match else "待确认"


def extract_due(line: str) -> str:
    for due in ["今天", "明天", "后天", "星期一", "星期二", "星期三", "星期四", "星期五", "六月中旬", "年底", "尽快"]:
        if due in line:
            return due
    return "待定"


def infer_deliverable(line: str) -> str:
    if "报价" in line:
        return "正式报价/报价公式"
    if "合同" in line:
        return "合同或条款确认"
    if "样" in line or "检测" in line:
        return "样品/检测数据"
    if "方案" in line or "设计" in line:
        return "技术方案/设计意见"
    return "待确认"


def is_uncertain_line(line: str) -> bool:
    if contains_any(line, ["不清楚", "不知道", "可能", "待定", "没搞懂", "不确定"]):
        return True
    if re.search(r"[\u3040-\u30ff]{2,}", line):
        return True
    if "***" in line or "****" in line or "[]" in line:
        return True
    if re.search(r"[A-Za-z]{8,}", line) and not line.startswith("#"):
        return True
    return False


def has_data_signal(line: str) -> bool:
    if re.search(r"\d", line):
        return True
    if re.search(r"[零一二三四五六七八九十百千万两]+(?:吨|万|块|元|分|点|号|%|公里)", line):
        return True
    if "百分" in line or "零点" in line or "一点" in line:
        return True
    return False


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def escape_cell(value: str) -> str:
    return value.replace("|", "｜").replace("\n", " ")


def as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def as_dict_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append({str(key): str(val) for key, val in item.items()})
    return rows
