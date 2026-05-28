from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Any


@dataclass
class Change:
    source: str
    target: str
    count: int
    status: str
    note: str
    rule_type: str


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "YAML support requires PyYAML. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def apply_replacement_rules(text: str, rules: dict[str, Any]) -> tuple[str, list[Change]]:
    changes: list[Change] = []
    for rule in rules.get("strong_replacements", []) or []:
        wrong = str(rule.get("wrong", ""))
        correct = str(rule.get("correct", ""))
        if not wrong or wrong == correct:
            continue
        count = text.count(wrong)
        if count == 0:
            continue
        text = text.replace(wrong, correct)
        changes.append(
            Change(
                source=wrong,
                target=correct,
                count=count,
                status=str(rule.get("status", "candidate")),
                note=str(rule.get("note", "")),
                rule_type="strong",
            )
        )
    return text, changes


def apply_regex_rules(text: str, rules: dict[str, Any]) -> tuple[str, list[Change]]:
    changes: list[Change] = []
    for rule in rules.get("regex_replacements", []) or []:
        pattern = str(rule.get("pattern", ""))
        replacement = str(rule.get("replacement", ""))
        if not pattern:
            continue
        compiled = re.compile(pattern)
        text, count = compiled.subn(replacement, text)
        if count == 0:
            continue
        changes.append(
            Change(
                source=pattern,
                target=replacement,
                count=count,
                status=str(rule.get("status", "candidate")),
                note=str(rule.get("note", "")),
                rule_type="regex",
            )
        )
    return text, changes


def apply_rules(text: str, rules_path: Path) -> tuple[str, list[Change]]:
    rules = load_yaml(rules_path)
    text, strong_changes = apply_replacement_rules(text, rules)
    text, regex_changes = apply_regex_rules(text, rules)
    return text, strong_changes + regex_changes


def changes_to_candidates(changes: list[Change], source_name: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for change in changes:
        if change.status != "candidate":
            continue
        item = asdict(change)
        item["source_file"] = source_name
        item["confidence"] = 0.75 if change.rule_type == "regex" else 0.85
        item["review_required"] = True
        candidates.append(item)
    return candidates

