from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import time
from typing import Any, Callable
from urllib import error, request


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_FLASH_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_PRO_MODEL = "deepseek-v4-pro"
ENV_LOADED = False


def deepseek_available() -> bool:
    load_dotenv_once()
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def load_text_if_exists(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def correct_with_deepseek(
    chunks: list[str],
    prompt_template: str,
    glossary: str,
    corrections: str,
    model: str,
    thinking: str,
    base_url: str | None = None,
    progress_callback: Callable[[int, int, int, int], None] | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    load_dotenv_once()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for deepseek mode.")

    url = normalize_chat_completions_url(base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL)
    corrected_chunks: list[str] = []
    changes: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    context_summary = ""
    done_chars = 0
    total_chars = sum(len(chunk) for chunk in chunks)

    for index, chunk in enumerate(chunks, start=1):
        if progress_callback:
            progress_callback(index, len(chunks), done_chars, total_chars)
        prompt = (
            prompt_template.replace("{glossary}", glossary[:4000])
            .replace("{corrections}", corrections[:4000])
            .replace("{context_summary}", context_summary[:1200])
            .replace("{chunk}", chunk)
        )
        data = call_deepseek(
            url=url,
            api_key=api_key,
            model=model,
            prompt=prompt,
            system_content="你是四川乐山矿业会议转写纠错助手。请只输出 JSON。",
            max_tokens=6000,
            thinking=thinking,
        )
        corrected = str(data.get("corrected_text") or "").strip()
        if not corrected:
            corrected = chunk
            uncertain.append(
                {
                    "text": f"第 {index} 段模型返回空 corrected_text，已回退为本地纠错结果。",
                    "reason": "DeepSeek 返回结构不完整或空文本",
                }
            )
        corrected_chunks.append(corrected)
        changes.extend(as_list(data.get("changes")))
        uncertain.extend(as_list(data.get("uncertain_items")))
        candidates.extend(as_list(data.get("learn_candidates")))
        context_summary = summarize_for_context(corrected, index)
        done_chars += len(chunk)
        if progress_callback:
            progress_callback(index, len(chunks), done_chars, total_chars)

    return "\n\n".join(corrected_chunks), changes, uncertain, candidates


def generate_semantic_minutes_with_deepseek(
    corrected_text: str,
    prompt_template: str,
    glossary: str,
    model: str,
    thinking: str,
    source_names: list[str],
    base_url: str | None = None,
) -> dict[str, Any]:
    load_dotenv_once()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for semantic minutes generation.")

    url = normalize_chat_completions_url(base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL)
    prompt = (
        prompt_template.replace("{glossary}", glossary[:6000])
        .replace("{source_names}", "、".join(source_names))
        .replace("{corrected_text}", corrected_text)
    )
    return call_deepseek(
        url=url,
        api_key=api_key,
        model=model,
        prompt=prompt,
        system_content="你是专业矿业会议纪要助手。请根据全文语义归纳会议纪要, 只输出 JSON。",
        max_tokens=10000,
        thinking=thinking,
    )


def normalize_chat_completions_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def load_dotenv_once() -> None:
    global ENV_LOADED
    if ENV_LOADED:
        return
    ENV_LOADED = True
    for path in dotenv_candidates():
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def dotenv_candidates() -> list[Path]:
    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    workspace_dir = skill_dir.parent
    return [
        Path.cwd() / ".env",
        workspace_dir / ".env",
        skill_dir / ".env",
    ]


def call_deepseek(
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    system_content: str,
    max_tokens: int,
    thinking: str,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "thinking": {"type": thinking},
    }
    if thinking == "enabled":
        payload["reasoning_effort"] = "high"
    else:
        payload["temperature"] = 0.1
    raw = post_with_retries(url=url, api_key=api_key, payload=payload)
    envelope = json.loads(raw)
    content = envelope["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"corrected_text": content, "changes": [], "uncertain_items": [], "learn_candidates": []}


def post_with_retries(url: str, api_key: str, payload: dict[str, Any], attempts: int = 3) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        req = request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=300) as response:
                return response.read().decode("utf-8")
        except (TimeoutError, http.client.IncompleteRead, http.client.HTTPException, error.URLError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(2 ** attempt)
    raise RuntimeError(f"DeepSeek request failed after {attempts} attempts: {last_error}") from last_error


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def summarize_for_context(text: str, index: int) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return f"已处理第 {index} 段。最近内容摘要: {compact[:800]}"
