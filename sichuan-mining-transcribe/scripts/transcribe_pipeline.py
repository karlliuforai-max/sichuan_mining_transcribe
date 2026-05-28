from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from apply_rules import apply_rules, changes_to_candidates
from extract_text import combine_sources, extract_inputs
from generate_minutes import (
    extract_action_items,
    extract_key_data,
    extract_uncertain_items,
    generate_minutes,
    write_action_items_csv,
    write_key_data_csv,
    write_uncertain_markdown,
)
from llm_correct import correct_with_deepseek, deepseek_available, load_text_if_exists
from llm_correct import DEFAULT_DEEPSEEK_FLASH_MODEL, DEFAULT_DEEPSEEK_PRO_MODEL
from llm_correct import generate_semantic_minutes_with_deepseek
from normalize_text import normalize_text, split_chunks
from update_knowledge import persist_candidates, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Correct Sichuan mining transcripts and generate minutes.")
    parser.add_argument("--input", required=True, help="Input PDF/TXT/MD file or directory.")
    parser.add_argument("--output", required=True, help="Output directory.")
    parser.add_argument("--mode", choices=["local", "agent", "deepseek", "auto"], default="deepseek")
    parser.add_argument("--progress", choices=["text", "jsonl", "none"], default="text")
    parser.add_argument("--knowledge-dir", default=str(SKILL_DIR / "knowledge"))
    parser.add_argument("--learn-policy", choices=["off", "candidate", "auto-confirmed"], default="candidate")
    parser.add_argument("--template", choices=["simple", "business", "technical"], default="business")
    parser.add_argument("--deepseek-tier", choices=["flash", "pro"], default="flash")
    parser.add_argument("--deepseek-thinking", choices=["auto", "enabled", "disabled"], default="auto")
    parser.add_argument("--model", default=None, help="Override the DeepSeek model. Defaults to tier model.")
    parser.add_argument("--max-chars", type=int, default=4500)
    return parser.parse_args()


class Progress:
    def __init__(self, output_dir: Path, mode: str) -> None:
        self.mode = mode
        self.log_path = output_dir / "run_log.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_path.open("w", encoding="utf-8")
        self.started_at = time.monotonic()
        self.stage_started_at: dict[str, float] = {}

    def emit(self, stage: str, message: str, done_chars: int = 0, total_chars: int = 0, **extra: Any) -> None:
        now = time.monotonic()
        self.stage_started_at.setdefault(stage, now)
        event = {
            "stage": stage,
            "message": message,
            "done_chars": done_chars,
            "total_chars": total_chars,
            "elapsed_seconds": round(now - self.started_at, 1),
            "stage_elapsed_seconds": round(now - self.stage_started_at[stage], 1),
            **extra,
        }
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        self.log_file.write(line + "\n")
        self.log_file.flush()
        if self.mode == "jsonl":
            print(line, flush=True)
        elif self.mode == "text":
            print(format_progress_text(event), flush=True)

    def close(self) -> None:
        self.log_file.close()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    knowledge_dir = Path(args.knowledge_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    progress = Progress(output_dir=output_dir, mode=args.progress)
    try:
        run_pipeline(args, input_path, output_dir, knowledge_dir, progress)
    finally:
        progress.close()
    return 0


def run_pipeline(
    args: argparse.Namespace,
    input_path: Path,
    output_dir: Path,
    knowledge_dir: Path,
    progress: Progress,
) -> None:
    model = resolve_model(args)
    thinking = resolve_thinking(args, model)
    progress.emit("extracting", "正在提取文本", input=str(input_path))
    sources = extract_inputs(input_path)
    extracted = combine_sources(sources)
    write_text(output_dir / "01_extracted.txt", extracted)
    total_chars = len(extracted)
    progress.emit("extracting", "文本提取完成", done_chars=total_chars, total_chars=total_chars, files=len(sources))

    progress.emit("normalizing", "正在清洗文本", done_chars=0, total_chars=total_chars)
    normalized = normalize_text(extracted)
    write_text(output_dir / "02_normalized.txt", normalized)
    progress.emit("normalizing", "文本清洗完成", done_chars=len(normalized), total_chars=len(normalized))

    progress.emit("correcting", "正在应用本地纠错规则", done_chars=0, total_chars=len(normalized))
    rules_path = SKILL_DIR / "references" / "correction_rules.yml"
    corrected, rule_changes = apply_rules(normalized, rules_path)
    rule_change_dicts = [asdict(change) for change in rule_changes]
    candidates = changes_to_candidates(rule_changes, source_name=input_path.name)
    progress.emit(
        "correcting",
        "本地纠错完成",
        done_chars=len(corrected),
        total_chars=len(corrected),
        rule_changes=sum(change.count for change in rule_changes),
    )

    llm_changes: list[dict[str, Any]] = []
    llm_uncertain: list[dict[str, Any]] = []
    llm_candidates: list[dict[str, Any]] = []
    if should_use_deepseek(args.mode):
        progress.emit("llm", "正在调用 DeepSeek 进行上下文纠错", done_chars=0, total_chars=len(corrected), model=model, thinking=thinking)
        chunks = split_chunks(corrected, max_chars=args.max_chars)
        prompt_template = extract_prompt_template(SKILL_DIR / "references" / "prompt_templates.md")
        glossary = load_text_if_exists(SKILL_DIR / "references" / "glossary.yml")
        confirmed = load_text_if_exists(knowledge_dir / "confirmed_corrections.jsonl")
        corrected, llm_changes, llm_uncertain, llm_candidates = correct_with_deepseek(
            chunks=chunks,
            prompt_template=prompt_template,
            glossary=glossary,
            corrections=confirmed,
            model=model,
            thinking=thinking,
            progress_callback=lambda idx, total, done, all_chars: progress.emit(
                "llm",
                f"DeepSeek 纠错进行中：第 {idx}/{total} 段，已完成 {done}/{all_chars} 字",
                done_chars=done,
                total_chars=all_chars,
                chunk_index=idx,
                chunk_total=total,
                model=model,
                thinking=thinking,
            ),
        )
        candidates.extend(llm_candidates)
        progress.emit("llm", "DeepSeek 纠错完成", done_chars=len(corrected), total_chars=len(corrected), chunks=len(chunks))
    elif args.mode == "auto" and not deepseek_available():
        progress.emit("llm", "auto 模式未检测到 DEEPSEEK_API_KEY，已使用本地结果", done_chars=len(corrected), total_chars=len(corrected))
    elif args.mode == "agent":
        progress.emit("llm", "agent 模式在 CLI 中按本地规则输出，调用方 agent 可继续人工/模型复核", done_chars=len(corrected), total_chars=len(corrected))

    corrected_md = build_corrected_markdown(corrected, rule_change_dicts, llm_changes)
    write_text(output_dir / "03_corrected.md", corrected_md)

    progress.emit("minutes", "正在生成会议纪要和结构化表格", done_chars=0, total_chars=len(corrected))
    key_data = extract_key_data(corrected)
    action_items = extract_action_items(corrected)
    uncertain_items = extract_uncertain_items(corrected, rule_change_dicts, llm_uncertain)
    semantic_minutes = None
    if should_use_deepseek(args.mode):
        progress.emit("minutes", "正在调用 DeepSeek 进行全文语义纪要生成", done_chars=0, total_chars=len(corrected), model=model, thinking=thinking)
        source_names = [source.path.name for source in sources]
        semantic_prompt = extract_prompt_template(
            SKILL_DIR / "references" / "prompt_templates.md",
            heading="## DeepSeek Minutes Prompt",
        )
        glossary = load_text_if_exists(SKILL_DIR / "references" / "glossary.yml")
        semantic_minutes = generate_semantic_minutes_with_deepseek(
            corrected_text=corrected,
            prompt_template=semantic_prompt,
            glossary=glossary,
            model=model,
            thinking=thinking,
            source_names=source_names,
        )
        write_text(output_dir / "09_semantic_minutes.json", json.dumps(semantic_minutes, ensure_ascii=False, indent=2) + "\n")
        key_data = normalize_semantic_rows(
            semantic_minutes.get("key_data"),
            ["source", "type", "value", "context", "needs_review"],
            defaults={"source": "semantic", "needs_review": "是"},
        ) or key_data[:40]
        action_items = normalize_semantic_rows(
            semantic_minutes.get("action_items"),
            ["source", "item", "owner", "due", "deliverable"],
            defaults={"source": "semantic", "owner": "待确认", "due": "待定", "deliverable": "待确认"},
        ) or action_items[:20]
        uncertain_items = normalize_semantic_rows(
            semantic_minutes.get("uncertain_items"),
            ["source", "text", "reason"],
            defaults={"source": "semantic"},
        ) or uncertain_items[:30]
        progress.emit("minutes", "DeepSeek 语义纪要生成完成", done_chars=len(corrected), total_chars=len(corrected))

    minutes = generate_minutes(
        corrected_text=corrected,
        key_data=key_data,
        action_items=action_items,
        uncertain_items=uncertain_items,
        template=args.template,
        semantic=semantic_minutes,
        source_names_override=[source.path.name for source in sources],
    )
    write_text(output_dir / "04_minutes.md", minutes)
    write_key_data_csv(output_dir / "05_key_data.csv", key_data)
    write_action_items_csv(output_dir / "06_action_items.csv", action_items)
    write_uncertain_markdown(output_dir / "07_uncertain_items.md", uncertain_items)
    persist_candidates(
        output_path=output_dir / "08_learn_candidates.jsonl",
        candidates=candidates,
        knowledge_dir=knowledge_dir,
        learn_policy=args.learn_policy,
    )
    write_manifest(output_dir, args, input_path, sources, rule_change_dicts, key_data, action_items, uncertain_items, model, thinking)
    progress.emit("done", "处理完成", done_chars=len(corrected), total_chars=len(corrected), output=str(output_dir))


def should_use_deepseek(mode: str) -> bool:
    if mode == "deepseek":
        return True
    if mode == "auto" and deepseek_available():
        return True
    return False


def format_progress_text(event: dict[str, Any]) -> str:
    stage_labels = {
        "extracting": "提取",
        "normalizing": "清洗",
        "correcting": "本地纠错",
        "llm": "DeepSeek",
        "minutes": "纪要",
        "done": "完成",
    }
    stage = stage_labels.get(str(event.get("stage", "")), str(event.get("stage", "")))
    elapsed = float(event.get("elapsed_seconds", 0.0))
    stage_elapsed = float(event.get("stage_elapsed_seconds", 0.0))
    done_chars = int(event.get("done_chars", 0) or 0)
    total_chars = int(event.get("total_chars", 0) or 0)
    message = str(event.get("message", "处理中"))
    if total_chars > 0:
        percent = min(100.0, done_chars / total_chars * 100)
        progress_detail = f"进度 {done_chars}/{total_chars} 字，{percent:.1f}%"
        if "已完成" in message:
            progress_detail = f"{percent:.1f}%"
        return (
            f"[总耗时 {elapsed:.1f}s | 本阶段 {stage_elapsed:.1f}s] "
            f"{stage}：{message}（{progress_detail}）"
        )
    return f"[总耗时 {elapsed:.1f}s | 本阶段 {stage_elapsed:.1f}s] {stage}：{message}"


def resolve_model(args: argparse.Namespace) -> str:
    if args.model:
        return args.model
    if args.deepseek_tier == "pro":
        return DEFAULT_DEEPSEEK_PRO_MODEL
    return DEFAULT_DEEPSEEK_FLASH_MODEL


def resolve_thinking(args: argparse.Namespace, model: str) -> str:
    if args.deepseek_thinking != "auto":
        return args.deepseek_thinking
    if args.deepseek_tier == "pro" or model == DEFAULT_DEEPSEEK_PRO_MODEL or model.endswith("-pro"):
        return "enabled"
    return "disabled"


def extract_prompt_template(path: Path, heading: str | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    if heading:
        heading_start = text.find(heading)
        if heading_start == -1:
            raise ValueError(f"Prompt heading not found: {heading}")
        next_heading = text.find("\n## ", heading_start + len(heading))
        text = text[heading_start: next_heading if next_heading != -1 else len(text)]
    marker = "```text"
    start = text.find(marker)
    if start == -1:
        return text
    start += len(marker)
    end = text.find("```", start)
    return text[start:end].strip() if end != -1 else text[start:].strip()


def normalize_semantic_rows(value: Any, fields: list[str], defaults: dict[str, str]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        row = {field: str(item.get(field, defaults.get(field, ""))) for field in fields}
        rows.append(row)
    return rows


def build_corrected_markdown(
    corrected: str,
    rule_changes: list[dict[str, Any]],
    llm_changes: list[dict[str, Any]],
) -> str:
    sections = [
        "# 纠错全文",
        "",
        "## 纠错说明",
        f"- 本地规则变更类型数：{len(rule_changes)}",
        f"- 模型变更记录数：{len(llm_changes)}",
        "- 关键数字、人名、公司名、矿山名和合同条件仍需结合原文复核。",
        "",
        "## 正文",
        "",
        corrected.strip(),
        "",
    ]
    return "\n".join(sections)


def write_manifest(
    output_dir: Path,
    args: argparse.Namespace,
    input_path: Path,
    sources: list[Any],
    rule_changes: list[dict[str, Any]],
    key_data: list[dict[str, str]],
    action_items: list[dict[str, str]],
    uncertain_items: list[dict[str, str]],
    model: str,
    thinking: str,
) -> None:
    manifest = {
        "input": str(input_path),
        "mode": args.mode,
        "template": args.template,
        "learn_policy": args.learn_policy,
        "deepseek_used": should_use_deepseek(args.mode),
        "model": model if should_use_deepseek(args.mode) else None,
        "deepseek_tier": args.deepseek_tier,
        "deepseek_thinking": thinking if should_use_deepseek(args.mode) else None,
        "source_files": [str(source.path) for source in sources],
        "counts": {
            "rule_change_types": len(rule_changes),
            "rule_change_total": sum(int(change.get("count", 0)) for change in rule_changes),
            "key_data_rows": len(key_data),
            "action_item_rows": len(action_items),
            "uncertain_item_rows": len(uncertain_items),
        },
        "outputs": [
            "01_extracted.txt",
            "02_normalized.txt",
            "03_corrected.md",
            "04_minutes.md",
            "05_key_data.csv",
            "06_action_items.csv",
            "07_uncertain_items.md",
            "08_learn_candidates.jsonl",
            "run_log.jsonl",
        ],
    }
    if (output_dir / "09_semantic_minutes.json").exists():
        manifest["outputs"].append("09_semantic_minutes.json")
    write_text(output_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    write_jsonl(output_dir / "rule_changes.jsonl", rule_changes)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
