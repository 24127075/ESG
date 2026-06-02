"""Cleaning & semantic chunking (SDAD §8).

PhoBERT has a hard 256-token limit; we cap chunks at MAX_TOKENS = 200, leaving
54 tokens of headroom for special tokens ([CLS], [SEP]) and an injected
heading_context. We deliberately do NOT hard-filter character classes, so
chemical/energy figures like ``CO2`` and ``kWh`` survive.

Token counting prefers the real PhoBERT tokenizer (``vinai/phobert-base``).
If ``transformers`` (or the model) is unavailable, we transparently fall back
to a whitespace word count so the pipeline still runs offline — the fallback
is conservative and emits a one-time warning.
"""
from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

MAX_TOKENS = 200
PHOBERT_MODEL = "vinai/phobert-base"

# Heading detection: Roman numerals (I..X) or decimal section numbers
# ("1.", "2.3", "10.1.") followed by title text.
_HEADING_RE = re.compile(
    r"(?i)^((?:I{1,3}|IV|V|VI{0,3}|IX|X)\.|\d{1,2}\.|\d{1,2}\.\d{1,2}\.?)\s+.*$"
)

_tokenizer = None
_tokenizer_loaded = False


def _get_tokenizer():
    """Load the PhoBERT tokenizer once; return None if unavailable."""
    global _tokenizer, _tokenizer_loaded
    if _tokenizer_loaded:
        return _tokenizer
    _tokenizer_loaded = True
    try:
        from transformers import AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained(PHOBERT_MODEL)
    except Exception as exc:  # noqa: BLE001 - missing pkg or offline model
        logger.warning(
            "PhoBERT tokenizer unavailable (%s); falling back to whitespace "
            "token counting. Install `transformers` and cache %s for exact counts.",
            exc,
            PHOBERT_MODEL,
        )
        _tokenizer = None
    return _tokenizer


def count_tokens(text: str) -> int:
    """Token count under the PhoBERT tokenizer (subword), or whitespace fallback."""
    tok = _get_tokenizer()
    if tok is not None:
        return len(tok.encode(text, add_special_tokens=False))
    return len(text.split())


def is_heading(paragraph: str) -> bool:
    """True when a paragraph looks like a numbered section heading."""
    return bool(_HEADING_RE.match(paragraph))


def clean_text_advanced(raw_text: str) -> list[str]:
    """Normalise and split raw text into clean, non-empty paragraphs.

    Steps: NFC-normalise; join intra-sentence line breaks while keeping
    paragraph breaks; drop page/footer noise (e.g. "Trang 12 / 100").
    """
    text = unicodedata.normalize("NFC", raw_text)
    # Remove header/footer page-number noise (e.g. "Trang 12 / 100") FIRST,
    # while footers are still on their own physical lines. (The SDAD lists this
    # after the newline-collapse step, but at that point a footer preceded by a
    # single newline has already been merged into the previous sentence and
    # escapes the ^...$ match — so we run it before the collapse to honour the
    # documented intent of dropping footer noise.)
    text = re.sub(
        r"(?i)^\s*(trang|page)\s*\d+.*$", "", text, flags=re.MULTILINE
    )
    # Collapse a single newline (mid-sentence wrap) into a space; keep blank-line
    # paragraph breaks intact.
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    return [line.strip() for line in text.split("\n") if line.strip()]


def chunk_document(paragraphs: list[str]) -> list[dict]:
    """Greedy semantic chunking bounded by MAX_TOKENS, heading-aware.

    A new heading flushes the current chunk and becomes the heading context for
    subsequent chunks. Within a section, paragraphs accumulate until adding the
    next one would exceed MAX_TOKENS, at which point the chunk is flushed.

    Returns dicts of ``{"heading", "text", "token_count"}``.
    """
    chunks: list[dict] = []
    current_heading = "Thong tin chung"
    current_text = ""
    current_tokens = 0

    def save_chunk() -> None:
        nonlocal current_text, current_tokens
        if current_text.strip():
            chunks.append(
                {
                    "heading": current_heading,
                    "text": current_text.strip(),
                    "token_count": current_tokens,
                }
            )
        current_text = ""
        current_tokens = 0

    for para in paragraphs:
        if is_heading(para):
            save_chunk()
            current_heading = para
        else:
            para_tokens = count_tokens(para)
            if current_tokens + para_tokens > MAX_TOKENS:
                save_chunk()
                current_text = para
                current_tokens = para_tokens
            else:
                current_text = f"{current_text} {para}".strip()
                current_tokens += para_tokens

    save_chunk()
    return chunks
