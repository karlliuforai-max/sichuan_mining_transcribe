from __future__ import annotations

import re
import unicodedata


NOISE_LINE_PATTERNS = [
    re.compile(r"^\s*$"),
]

RADICAL_TRANSLATION = str.maketrans(
    {
        "⺓": "角",
        "⺠": "民",
        "⻁": "虎",
        "⻅": "见",
        "⻆": "角",
        "⻋": "车",
        "⻓": "长",
        "⻔": "门",
        "⻘": "青",
        "⻙": "韦",
        "⻚": "页",
        "⻛": "风",
        "⻜": "飞",
        "⻢": "马",
        "⻤": "鬼",
        "⻥": "鱼",
        "⻩": "黄",
        "⻬": "齐",
        "⻰": "龙",
    }
)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(RADICAL_TRANSLATION)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = normalize_speaker_labels(text)
    text = normalize_common_punctuation(text)
    return text.strip() + "\n"


def normalize_speaker_labels(text: str) -> str:
    text = re.sub(r"发言人\s*(\d+)?\s+(\d{2}:\d{2}:\d{2})", r"发言人 \1 \2", text)
    text = re.sub(r"发言人\s+(\d{2}:\d{2}:\d{2})", r"发言人 \1", text)
    text = re.sub(r"发言人\s+ +", "发言人 ", text)
    return text


def normalize_common_punctuation(text: str) -> str:
    text = text.replace(" ,", "，").replace(" .", "。")
    text = re.sub(r"([。！？])\s+", r"\1\n", text)
    return text


def split_chunks(text: str, max_chars: int = 4500, overlap: int = 250) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    paragraphs = re.split(r"(\n{2,}|(?=^# Source:))", text, flags=re.MULTILINE)
    merged: list[str] = []
    buffer = ""
    for part in paragraphs:
        if not part:
            continue
        if len(buffer) + len(part) <= max_chars:
            buffer += part
            continue
        if buffer.strip():
            merged.append(buffer.strip())
        buffer = part
    if buffer.strip():
        merged.append(buffer.strip())

    chunks: list[str] = []
    for chunk in merged:
        if len(chunk) <= max_chars:
            chunks.append(chunk)
            continue
        start = 0
        while start < len(chunk):
            end = min(start + max_chars, len(chunk))
            chunks.append(chunk[start:end].strip())
            if end == len(chunk):
                break
            start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk]
