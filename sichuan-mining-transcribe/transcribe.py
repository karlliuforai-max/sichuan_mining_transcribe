#!/usr/bin/env python3
"""四川矿业会议转写纠错与会议纪要生成。

用法:
    python transcribe.py "会议.txt"                 # 默认 DeepSeek flash 并行纠错
    python transcribe.py "会议.pdf" --tier pro      # 重要会议用 pro
    python transcribe.py "会议.txt" --local         # 离线模式,只用本地规则
    python transcribe.py review                     # 审核高频候选,晋升为确认规则

产出(输出目录下):
    纠错全文.md  会议纪要.md  疑点清单.md
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import http.client
import json
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from urllib import error, request

SKILL_DIR = Path(__file__).resolve().parent
KNOWLEDGE_DIR = SKILL_DIR / "knowledge"
GLOSSARY_PATH = KNOWLEDGE_DIR / "glossary.yml"
RULES_PATH = KNOWLEDGE_DIR / "confirmed_rules.yml"
CANDIDATES_PATH = KNOWLEDGE_DIR / "candidates.jsonl"

DEEPSEEK_MODELS = {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}
DEFAULT_BASE_URL = "https://api.deepseek.com"
REQUEST_TIMEOUT = 90
REQUEST_RETRIES = 2
HEARTBEAT_SECONDS = 5

CJK_RE = re.compile(r"[一-鿿]")
DIGIT_RE = re.compile(r"[0-9０-９]")


# ---------------------------------------------------------------- 环境与知识

def load_dotenv() -> None:
    for env_path in (Path.cwd() / ".env", SKILL_DIR.parent / ".env", SKILL_DIR / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_candidates() -> list[dict]:
    if not CANDIDATES_PATH.exists():
        return []
    rows = []
    for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def save_candidates(rows: list[dict]) -> None:
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    CANDIDATES_PATH.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


# ---------------------------------------------------------------- 提取与清洗

SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}


def extract_input(input_path: Path) -> str:
    if input_path.is_dir():
        files = sorted(
            p for p in input_path.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        )
        if not files:
            raise SystemExit(f"目录中没有找到 PDF/TXT/MD 文件: {input_path}")
        return "\n\n".join(extract_file(p) for p in files)
    if not input_path.is_file():
        raise SystemExit(f"输入文件不存在: {input_path}")
    if input_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise SystemExit(f"不支持的文件类型(仅支持 PDF/TXT/MD): {input_path.name}")
    return extract_file(input_path)


def extract_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise SystemExit("处理 PDF 需要 pypdf,请先运行: pip install pypdf PyYAML")
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        if not text.strip():
            raise SystemExit(f"PDF 提取结果为空,可能是扫描件,需要先做 OCR: {path.name}")
        return text
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def split_chunks(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    buffer = ""
    for para in text.split("\n"):
        candidate = f"{buffer}\n{para}" if buffer else para
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
        while len(para) > max_chars:
            chunks.append(para[:max_chars])
            para = para[max_chars:]
        buffer = para
    if buffer.strip():
        chunks.append(buffer)
    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------- 本地规则

def apply_local_rules(text: str, rules: dict) -> tuple[str, list[dict]]:
    changes = []
    for item in rules.get("replacements", []) or []:
        wrong, right = str(item.get("wrong", "")), str(item.get("right", ""))
        if not wrong or not right or wrong not in text:
            continue
        count = text.count(wrong)
        text = text.replace(wrong, right)
        changes.append({"wrong": wrong, "right": right, "count": count})
    return text, changes


# ---------------------------------------------------------------- DeepSeek

def deepseek_chat(system: str, user: str, model: str, max_tokens: int) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY,请配置 .env 或使用 --local 模式")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "thinking": {"type": "disabled"},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    last_error: BaseException | None = None
    for attempt in range(1, REQUEST_RETRIES + 2):
        req = request.Request(
            url,
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                envelope = json.loads(response.read().decode("utf-8"))
            return str(envelope["choices"][0]["message"]["content"] or "")
        except (TimeoutError, http.client.HTTPException, error.URLError, KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt <= REQUEST_RETRIES:
                time.sleep(2 * attempt)
    raise RuntimeError(f"DeepSeek 请求失败({REQUEST_RETRIES + 1} 次): {last_error}")


def strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text.rstrip())
    return text.strip()


# ---------------------------------------------------------------- 纠错提示词

CORRECTION_SYSTEM = (
    "你是四川乐山矿业会议转写纠错助手。"
    "只输出纠错后的正文,不要任何解释、前言、总结或代码块标记。"
)

CORRECTION_PROMPT = """以下是矿业会议语音转写文本的一个片段,发言人混用普通话、四川话和乐山话,存在同音/近音误识。请逐句纠错。

【本场会议背景(仅帮助你判断术语含义,请勿把背景内容写进输出)】
{overview}

【纠错要求】
1. 只修正明显的误识词,保持原句结构、语气词和口语风格;禁止改写、扩写、概括或删减内容。
2. 重点修正矿业和商务术语的同音误识,结合上方会议背景判断该用哪个术语(参考下方术语表)。
3. 不得改动任何数字、金额、日期、百分比;不得改动人名、公司名、矿山名(除非纠错对照中明确列出)。
4. 保留原文的发言人标记和时间戳。
5. 无法确定的内容保持原样,不要猜测。

【矿业术语表(正确写法)】
{glossary}

【历史确认纠错对照(可直接采用)】
{hints}

【高频候选纠错(供参考,需结合上下文判断)】
{candidates}

【待纠错文本】
{chunk}"""

OVERVIEW_SYSTEM = "你是矿业会议分析助手。只输出要求的简短概括,不要前言、不要解释。"

OVERVIEW_PROMPT = """下面是一场矿业会议转写的开头部分。请用 2~3 句话概括,作为后续逐段纠错的背景:
- 会议主题与目的
- 涉及的主要矿种 / 产品 / 业务环节(如锂矿、精粉、套保、采矿权等)
- 可识别的参会方或称呼(如"曹总""建发")

请简洁,不要展开。

【会议主题(来自文件名,供参考)】{topic}

【转写开头】
{head}"""

SUMMARY_SYSTEM = "你是专业的矿业会议纪要助手,擅长从口语转写中提炼商务要点。只输出要求的内容,不要前言。"

SUMMARY_PROMPT = """以下是一场矿业会议纠错后转写全文的第 {index}/{total} 部分。请提炼本部分的会议要点,用 Markdown 列表输出,涵盖(有则写,无则省略):
- 本部分讨论主线:用 1~2 句话概括本部分从什么话题谈到什么话题,推进到了什么程度(这条必写,用于还原会议进程)
- 讨论议题与各方立场(尽量用说话人的称呼如"曹总",不要写"发言人N")
- 关键数据:与交易决策相关的价格、品位、数量、金额、比例、日期 —— 必须逐字保留原文数字,并在每个数字后附上它所在的原文短句,格式如:`品位 0.8(原文:"实际平均品位只有零点八几")`
- 达成的共识或决定
- 商务条件:付款方式、定金、运输、加工费等
- 行动项:谁、做什么、什么时间
- 分歧、风险或待确认事项

【转写文本】
{section}"""

MINUTES_PROMPT = """请根据以下分段会议要点,汇总生成一份完整的矿业会议纪要。

【会议元信息】
- 会议名称:{meeting_name}
- 会议日期:{meeting_date}
- 议题(来自文件名):{meeting_topic}

【纠错阶段已标记的存疑内容】
{uncertain_digest}

要求:
1. 用 Markdown 输出,结构为:
   # 会议纪要:{meeting_name}
   ## 摘要(3 行以内,让人快速抓住会议结论和下一步)
   ## 会议信息(日期填上方会议日期;参会方只列能识别出称呼的人,如"曹总、汤总";无法识别的统一写一句"另有若干未具名发言人",绝不要逐个列"发言人N")
   ## 核心结论与共识
   ## 各方观点(按参会方分条,每人概括其核心立场、主张和关切,1~3 句;无法识别姓名但立场重要的,用"某参会方"概括)
   ## 关键数据(Markdown 表格:类别 | 数值 | 原文摘录 | 说明)
   ## 商务条件
   ## 行动项(Markdown 表格:事项 | 负责方 | 时间 | 备注)
   ## 风险与分歧
   ## 待人工复核疑点(汇总上方存疑内容,以及数字、人名、条款等需核对原文之处)
   ## 会议进程回顾(按时间顺序还原会议脉络:依据各部分的"讨论主线",每个阶段一条,格式"阶段N:讨论了什么 → 达成/遗留了什么";让没参会的人能看懂会议是如何一步步推进的)
2. 关键数据表必须精简:只保留与核心结论、商务条件、风险分歧直接相关的商务数据(价格、品位、数量、付款、期限、罚则等);同类数据合并为一行(如多个品位写区间或并列);删除与交易决策无关的背景数字(如 GDP、工程量明细、闲谈中的金额);整表一般不超过 15 行。
3. 所有数字、金额必须来自要点原文,不得推算或编造;"原文摘录"列填该数据对应的原话片段。
4. 不要给每一项都加"待核实"。只对以下两类标注"(待核实)":① 上方存疑列表中提到的内容;② 你确实判断转写可能有歧义的数字或名称。其余有把握的内容直接干净呈现。
5. 语言简洁正式,可直接发给参会方。

【分段会议要点】
{summaries}"""


# ---------------------------------------------------------------- 并行纠错

def build_correction_context() -> dict:
    glossary = load_yaml(GLOSSARY_PATH)
    terms: list[str] = []
    for values in (glossary.get("domains") or {}).values():
        terms.extend(str(v) for v in values or [])
    titles = [str(v) for v in glossary.get("people_titles") or []]

    rules = load_yaml(RULES_PATH)
    hints = [
        f"{item['wrong']} → {item['right']}" + (f"({item['note']})" if item.get("note") else "")
        for item in (rules.get("context_hints") or [])
        if item.get("wrong") and item.get("right")
    ]
    confirmed_pairs = {
        (str(item.get("wrong")), str(item.get("right")))
        for item in (rules.get("replacements") or []) + (rules.get("context_hints") or [])
    }
    candidate_lines = []
    for row in sorted(load_candidates(), key=lambda r: -int(r.get("count", 0))):
        pair = (str(row.get("wrong")), str(row.get("right")))
        if len(row.get("meetings", [])) >= 2 and pair not in confirmed_pairs:
            candidate_lines.append(f"{pair[0]} → {pair[1]}(出现 {row.get('count', 0)} 次)")
        if len(candidate_lines) >= 30:
            break
    return {
        "glossary": ("、".join(terms) + ("\n常见称呼:" + "、".join(titles) if titles else ""))[:3000],
        "hints": ("\n".join(hints) or "(暂无)")[:2000],
        "candidates": ("\n".join(candidate_lines) or "(暂无)")[:1500],
        "rules": rules,
    }


def generate_overview(text: str, topic: str, model: str) -> str:
    """跑一次轻量请求,提炼整场会议背景,供每段纠错共享上下文。失败则回退为文件名议题。"""
    prompt = OVERVIEW_PROMPT.format(topic=topic or "(未知)", head=text[:4000])
    try:
        overview = strip_code_fence(deepseek_chat(OVERVIEW_SYSTEM, prompt, model, 600))
        return overview or (topic or "(无)")
    except RuntimeError:
        return topic or "(无)"


def correct_one_chunk(chunk: str, ctx: dict, model: str) -> tuple[str, str | None]:
    """返回 (纠错后文本, 失败原因或 None)。失败时回退为原文。"""
    prompt = CORRECTION_PROMPT.format(
        overview=ctx.get("overview") or "(无)",
        glossary=ctx["glossary"],
        hints=ctx["hints"],
        candidates=ctx["candidates"],
        chunk=chunk,
    )
    max_tokens = min(8000, max(2000, int(len(chunk) * 1.8)))
    try:
        result = strip_code_fence(deepseek_chat(CORRECTION_SYSTEM, prompt, model, max_tokens))
    except RuntimeError as exc:
        return chunk, f"请求失败: {exc}"
    if not result:
        return chunk, "模型返回空文本"
    ratio = len(result) / max(1, len(chunk))
    if ratio < 0.6 or ratio > 1.4:
        return chunk, f"模型返回长度异常(原文 {len(chunk)} 字,返回 {len(result)} 字)"
    return result, None


def correct_chunks(
    chunks: list[str], ctx: dict, model: str, workers: int, checkpoint_dir: Path, state: dict
) -> tuple[list[str], list[dict]]:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, str] = {}
    uncertain: list[dict] = []
    lock = threading.Lock()

    pending = []
    for i, chunk in enumerate(chunks):
        ck = checkpoint_dir / f"chunk_{i:03d}.txt"
        if ck.exists():
            results[i] = ck.read_text(encoding="utf-8")
            with lock:
                state["done_chunks"] += 1
                state["done_chars"] += len(chunk)
        else:
            pending.append(i)

    def work(i: int) -> None:
        corrected, fail_reason = correct_one_chunk(chunks[i], ctx, model)
        (checkpoint_dir / f"chunk_{i:03d}.txt").write_text(corrected, encoding="utf-8")
        with lock:
            results[i] = corrected
            state["done_chunks"] += 1
            state["done_chars"] += len(chunks[i])
            if fail_reason:
                uncertain.append(
                    {"text": f"第 {i + 1} 段未能完成模型纠错,已保留本地规则版本", "reason": fail_reason}
                )

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, i) for i in pending]
            for future in as_completed(futures):
                future.result()

    return [results[i] for i in range(len(chunks))], uncertain


# ---------------------------------------------------------------- 自进化:diff 提取候选

def extract_diff_pairs(before: str, after: str) -> tuple[list[tuple[str, str]], list[dict]]:
    """对比纠错前后文本,提取 (误识词, 正确词) 候选对;数字变更进疑点。"""
    pairs: list[tuple[str, str]] = []
    digit_flags: list[dict] = []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op != "replace":
            continue
        old, new = before[a1:a2], after[b1:b2]
        if DIGIT_RE.search(old) or DIGIT_RE.search(new):
            context = before[max(0, a1 - 15): min(len(before), a2 + 15)].replace("\n", " ")
            digit_flags.append(
                {"text": f"数字相关变更「{old}」→「{new}」,上下文: …{context}…", "reason": "涉及数字,需对照原文复核"}
            )
            continue
        if 1 <= len(old) <= 8 and 1 <= len(new) <= 8 and CJK_RE.search(old) and CJK_RE.search(new):
            pairs.append((old, new))
    return pairs, digit_flags


def update_candidates(pairs: list[tuple[str, str]], meeting_name: str) -> int:
    if not pairs:
        return 0
    rows = load_candidates()
    index = {(str(r.get("wrong")), str(r.get("right"))): r for r in rows}
    today = date.today().isoformat()
    for wrong, right in pairs:
        row = index.get((wrong, right))
        if row is None:
            row = {"wrong": wrong, "right": right, "count": 0, "meetings": [], "last_seen": today}
            rows.append(row)
            index[(wrong, right)] = row
        row["count"] = int(row.get("count", 0)) + 1
        meetings = row.setdefault("meetings", [])
        if meeting_name not in meetings:
            meetings.append(meeting_name)
            del meetings[:-20]
        row["last_seen"] = today
    save_candidates(rows)
    return len(set(pairs))


# ---------------------------------------------------------------- 纪要生成

def format_uncertain_digest(uncertain: list[dict]) -> str:
    if not uncertain:
        return "(纠错阶段未发现特定存疑项;请只对你确实判断有歧义的数字或名称标注待核实)"
    return "\n".join(f"- {item['text']}" for item in uncertain[:30])


def generate_minutes(
    corrected: str,
    meeting_name: str,
    meeting_date: str,
    meeting_topic: str,
    uncertain: list[dict],
    model: str,
    workers: int,
    state: dict,
) -> str:
    sections = split_chunks(corrected, max_chars=10000)
    state["minutes_total"] = len(sections)
    summaries: dict[int, str] = {}
    lock = threading.Lock()

    def summarize(i: int) -> None:
        prompt = SUMMARY_PROMPT.format(index=i + 1, total=len(sections), section=sections[i])
        text = strip_code_fence(deepseek_chat(SUMMARY_SYSTEM, prompt, model, 3000))
        with lock:
            summaries[i] = text
            state["minutes_done"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(summarize, i) for i in range(len(sections))]
        for future in as_completed(futures):
            future.result()

    joined = "\n\n".join(f"### 第 {i + 1} 部分要点\n{summaries[i]}" for i in range(len(sections)))
    prompt = MINUTES_PROMPT.format(
        meeting_name=meeting_name,
        meeting_date=meeting_date or "待补充",
        meeting_topic=meeting_topic or "待补充",
        uncertain_digest=format_uncertain_digest(uncertain),
        summaries=joined,
    )
    return strip_code_fence(deepseek_chat(SUMMARY_SYSTEM, prompt, model, 6000))


def extract_minutes_uncertain(minutes: str) -> list[dict]:
    match = re.search(r"##\s*待人工复核疑点\s*\n(.*?)(?=\n##\s|\Z)", minutes, re.DOTALL)
    if not match:
        return []
    items = []
    for line in match.group(1).splitlines():
        line = line.strip().lstrip("-*").strip()
        if line:
            items.append({"text": line, "reason": "纪要生成时标记"})
    return items


# ---------------------------------------------------------------- 心跳进度

class Heartbeat:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.started = time.monotonic()

    def __enter__(self) -> "Heartbeat":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1)

    def elapsed_label(self) -> str:
        seconds = int(time.monotonic() - self.started)
        return f"{seconds // 60}分{seconds % 60:02d}秒"

    def run(self) -> None:
        while not self.stop_event.wait(HEARTBEAT_SECONDS):
            stage = self.state.get("stage", "")
            if stage == "correct":
                done, total = self.state["done_chars"], self.state["total_chars"]
                pct = done / max(1, total) * 100
                stage_elapsed = time.monotonic() - self.state.get("stage_started", self.started)
                eta = ""
                if done > 0 and done < total:
                    remain = (total - done) / (done / max(1.0, stage_elapsed))
                    eta = f",预计还需约 {max(1, int(remain / 60))} 分钟" if remain > 50 else ",即将完成"
                print(
                    f"[{self.elapsed_label()}] 纠错中:{self.state['done_chunks']}/{self.state['total_chunks']} 段完成,"
                    f"{done}/{total} 字 ({pct:.0f}%){eta}",
                    flush=True,
                )
            elif stage == "minutes":
                done, total = self.state["minutes_done"], self.state.get("minutes_total", 0)
                if total and done < total:
                    print(f"[{self.elapsed_label()}] 纪要生成中:{done}/{total} 节摘要完成", flush=True)
                else:
                    print(f"[{self.elapsed_label()}] 纪要生成中:正在汇总成稿", flush=True)


# ---------------------------------------------------------------- 主流程

def parse_meeting_name(meeting_name: str) -> tuple[str, str]:
    """从文件名解析会议日期和议题,如 '06-12 矿山合作' → ('06-12', '矿山合作')。"""
    match = re.match(r"\s*(\d{4}[-.年]\d{1,2}[-.月]\d{1,2}|\d{1,2}[-.月]\d{1,2})\s*[日]?\s*", meeting_name)
    if not match:
        return "", meeting_name.strip()
    date_str = re.sub(r"[.年月]", "-", match.group(1)).rstrip("-")
    topic = meeting_name[match.end():].strip()
    return date_str, topic or meeting_name.strip()


def run(args: argparse.Namespace) -> None:
    load_dotenv()
    input_path = Path(args.input).expanduser().resolve()
    meeting_name = input_path.stem
    meeting_date, meeting_topic = parse_meeting_name(meeting_name)
    output_dir = (
        Path(args.output).expanduser().resolve()
        if args.output
        else SKILL_DIR / "outputs" / "runs" / f"{date.today().isoformat()}-{meeting_name}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / ".checkpoint"
    model = args.model or DEEPSEEK_MODELS[args.tier]

    use_llm = not args.local
    if use_llm and not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("缺少 DEEPSEEK_API_KEY:请在 .env 中配置,或使用 --local 离线模式")

    print(f"输入: {input_path.name}")
    print(f"输出目录: {output_dir}")

    # 1. 提取 + 清洗
    text = normalize_text(extract_input(input_path))
    print(f"文本提取完成,共 {len(text)} 字")

    # 2. 本地规则纠错
    ctx = build_correction_context()
    local_corrected, rule_changes = apply_local_rules(text, ctx["rules"])
    applied = sum(c["count"] for c in rule_changes)
    if applied:
        print(f"本地规则纠错完成,替换 {applied} 处")

    uncertain: list[dict] = []
    corrected = local_corrected
    minutes = ""

    state = {
        "stage": "",
        "done_chunks": 0,
        "total_chunks": 0,
        "done_chars": 0,
        "total_chars": len(local_corrected),
        "minutes_done": 0,
    }

    if use_llm:
        # 校验断点是否属于同一份输入,不一致就清掉
        text_sha = hashlib.sha256(f"{local_corrected}|{args.max_chars}".encode("utf-8")).hexdigest()
        meta_path = checkpoint_dir / "meta.json"
        if meta_path.exists():
            try:
                if json.loads(meta_path.read_text(encoding="utf-8")).get("sha") != text_sha:
                    shutil.rmtree(checkpoint_dir)
            except (json.JSONDecodeError, OSError):
                shutil.rmtree(checkpoint_dir, ignore_errors=True)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps({"sha": text_sha}), encoding="utf-8")

        chunks = split_chunks(local_corrected, max_chars=args.max_chars)
        state["total_chunks"] = len(chunks)
        resumed = sum(1 for i in range(len(chunks)) if (checkpoint_dir / f"chunk_{i:03d}.txt").exists())
        if resumed:
            print(f"检测到断点,跳过已完成的 {resumed}/{len(chunks)} 段")
        print("分析会议背景,建立全局上下文…")
        ctx["overview"] = generate_overview(local_corrected, meeting_topic, model)
        print(f"开始 DeepSeek 纠错({model},{args.workers} 路并行,共 {len(chunks)} 段)…")

        with Heartbeat(state) as heartbeat:
            state["stage"] = "correct"
            state["stage_started"] = time.monotonic()
            corrected_chunks, llm_uncertain = correct_chunks(
                chunks, ctx, model, args.workers, checkpoint_dir, state
            )
            corrected = "\n".join(corrected_chunks)
            uncertain.extend(llm_uncertain)
            print(f"[{heartbeat.elapsed_label()}] DeepSeek 纠错完成,共 {len(corrected)} 字")

            # 自进化:diff 提取候选纠错对
            pairs, digit_flags = extract_diff_pairs(local_corrected, corrected)
            uncertain.extend(digit_flags)
            new_pairs = update_candidates(pairs, meeting_name)
            if new_pairs:
                print(f"已从本次纠错中积累 {new_pairs} 组候选纠错对(自动用于后续运行)")

            # 纪要
            state["stage"] = "minutes"
            print(f"[{heartbeat.elapsed_label()}] 开始生成会议纪要…")
            minutes = generate_minutes(
                corrected, meeting_name, meeting_date, meeting_topic, uncertain, model, args.workers, state
            )
            uncertain.extend(extract_minutes_uncertain(minutes))
            state["stage"] = ""
            print(f"[{heartbeat.elapsed_label()}] 会议纪要生成完成")
    else:
        print("离线模式:跳过 DeepSeek 纠错与纪要生成,仅输出本地规则纠错结果")

    # 3. 写产出
    write_outputs(output_dir, meeting_name, corrected, minutes, uncertain, rule_changes)
    if use_llm:
        shutil.rmtree(checkpoint_dir, ignore_errors=True)

    print("\n处理完成,产出文件:")
    for name in ("纠错全文.md", "会议纪要.md", "疑点清单.md"):
        path = output_dir / name
        if path.exists():
            print(f"  {path}")
    promotable = count_promotable()
    if promotable:
        print(f"\n提示:已积累 {promotable} 组高频候选纠错,运行 `python transcribe.py review` 可审核晋升为确认规则。")


def write_outputs(
    output_dir: Path,
    meeting_name: str,
    corrected: str,
    minutes: str,
    uncertain: list[dict],
    rule_changes: list[dict],
) -> None:
    rule_lines = [f"- {c['wrong']} → {c['right']}({c['count']} 处)" for c in rule_changes]
    corrected_md = "\n".join(
        [
            f"# 纠错全文:{meeting_name}",
            "",
            f"生成时间:{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 本地规则替换",
            *(rule_lines or ["- 无"]),
            "",
            "## 正文",
            "",
            corrected.strip(),
            "",
        ]
    )
    (output_dir / "纠错全文.md").write_text(corrected_md, encoding="utf-8")

    if minutes:
        (output_dir / "会议纪要.md").write_text(minutes.strip() + "\n", encoding="utf-8")

    uncertain_lines = [f"- {item['text']}(原因:{item['reason']})" for item in uncertain]
    uncertain_md = "\n".join(
        [
            f"# 疑点清单:{meeting_name}",
            "",
            "以下内容建议对照录音或原始转写复核:",
            "",
            *(uncertain_lines or ["- 本次运行未发现需要人工复核的疑点。"]),
            "",
        ]
    )
    (output_dir / "疑点清单.md").write_text(uncertain_md, encoding="utf-8")


# ---------------------------------------------------------------- review 子命令

def count_promotable() -> int:
    rules = load_yaml(RULES_PATH)
    confirmed = {
        (str(i.get("wrong")), str(i.get("right")))
        for i in (rules.get("replacements") or []) + (rules.get("context_hints") or [])
    }
    return sum(
        1
        for row in load_candidates()
        if len(row.get("meetings", [])) >= 2
        and (str(row.get("wrong")), str(row.get("right"))) not in confirmed
    )


def cmd_review() -> None:
    import yaml

    rules = load_yaml(RULES_PATH)
    replacements = rules.setdefault("replacements", []) or []
    rules["replacements"] = replacements
    confirmed = {
        (str(i.get("wrong")), str(i.get("right")))
        for i in replacements + (rules.get("context_hints") or [])
    }

    promotable = [
        row
        for row in sorted(load_candidates(), key=lambda r: -int(r.get("count", 0)))
        if len(row.get("meetings", [])) >= 2
        and (str(row.get("wrong")), str(row.get("right"))) not in confirmed
    ]
    if not promotable:
        print("当前没有满足晋升条件的候选(需在 2 场以上会议中出现)。")
        return

    print(f"共 {len(promotable)} 组高频候选。y=采纳 / n=跳过 / d=删除该候选 / q=退出")
    print("注:单字候选只会进入上下文提示(由模型结合语境判断),不会成为全局硬替换。\n")
    candidates = load_candidates()
    hints = rules.setdefault("context_hints", []) or []
    rules["context_hints"] = hints
    accepted = 0
    for row in promotable:
        wrong, right = row["wrong"], row["right"]
        meetings = row.get("meetings", [])
        answer = input(
            f"「{wrong}」→「{right}」 出现 {row.get('count', 0)} 次,涉及 {len(meetings)} 场会议 [y/n/d/q]: "
        ).strip().lower()
        if answer == "q":
            break
        if answer == "y":
            note = f"review 采纳于 {date.today().isoformat()}"
            if len(wrong) <= 1:
                hints.append({"wrong": wrong, "right": right, "note": note + "(单字,仅作上下文提示)"})
            else:
                replacements.append({"wrong": wrong, "right": right, "note": note})
            accepted += 1
        elif answer == "d":
            candidates = [r for r in candidates if not (r.get("wrong") == wrong and r.get("right") == right)]

    if accepted:
        RULES_PATH.write_text(
            yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        print(f"\n已采纳 {accepted} 条进入 {RULES_PATH.name},后续运行将本地直接替换。")
    save_candidates(candidates)


# ---------------------------------------------------------------- 入口

def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "review":
        load_dotenv()
        cmd_review()
        return

    parser = argparse.ArgumentParser(description="四川矿业会议转写纠错与纪要生成")
    parser.add_argument("input", help="转写源文件(PDF/TXT/MD)或目录;或子命令 review")
    parser.add_argument("-o", "--output", help="输出目录,默认 outputs/runs/<日期-会议名>")
    parser.add_argument("--tier", choices=["flash", "pro"], default="flash", help="DeepSeek 档位,默认 flash")
    parser.add_argument("--model", help="覆盖 DeepSeek 模型名")
    parser.add_argument("--workers", type=int, default=6, help="并行请求数,默认 6")
    parser.add_argument("--max-chars", type=int, default=3500, help="纠错分段长度,默认 3500 字")
    parser.add_argument("--local", action="store_true", help="离线模式,只用本地规则,不调用 DeepSeek")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
