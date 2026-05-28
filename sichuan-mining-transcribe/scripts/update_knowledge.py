from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def dedupe_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (
            str(row.get("source", row.get("wrong", ""))),
            str(row.get("target", row.get("correct", ""))),
            str(row.get("note", row.get("reason", ""))),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def persist_candidates(
    output_path: Path,
    candidates: list[dict[str, Any]],
    knowledge_dir: Path | None,
    learn_policy: str,
) -> None:
    if learn_policy == "off":
        write_jsonl(output_path, [])
        return

    candidates = dedupe_candidates(candidates)
    write_jsonl(output_path, candidates)

    if not knowledge_dir or learn_policy != "candidate":
        return

    target = knowledge_dir / "learned_candidates.jsonl"
    existing = load_jsonl(target)
    write_jsonl(target, dedupe_candidates(existing + candidates))

