from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}


@dataclass(frozen=True)
class SourceText:
    path: Path
    kind: str
    text: str


def iter_input_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(
            path
            for path in input_path.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )
    else:
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    unsupported = [path for path in files if path.suffix.lower() not in SUPPORTED_SUFFIXES]
    if unsupported:
        names = ", ".join(str(path) for path in unsupported)
        raise ValueError(f"Unsupported input files: {names}")

    if not files:
        raise ValueError(f"No supported PDF/TXT/MD files found in: {input_path}")

    return files


def extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF support requires pypdf. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    reader = PdfReader(str(path))
    parts: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        parts.append(f"\n\n--- PAGE {index} ---\n{page_text}")
    return "".join(parts).strip()


def extract_plain_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_file(path: Path) -> SourceText:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return SourceText(path=path, kind="pdf", text=extract_pdf(path))
    if suffix in {".txt", ".md"}:
        return SourceText(path=path, kind=suffix[1:], text=extract_plain_text(path))
    raise ValueError(f"Unsupported file type: {path}")


def extract_inputs(input_path: Path) -> list[SourceText]:
    return [extract_file(path) for path in iter_input_files(input_path)]


def combine_sources(sources: list[SourceText]) -> str:
    chunks: list[str] = []
    for source in sources:
        chunks.append(
            "\n".join(
                [
                    f"# Source: {source.path.name}",
                    f"# Path: {source.path}",
                    f"# Kind: {source.kind}",
                    "",
                    source.text.strip(),
                ]
            )
        )
    return "\n\n".join(chunks).strip() + "\n"

