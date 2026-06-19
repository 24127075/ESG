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

# Leetspeak → letter map for OCR/obfuscation artefacts (e.g. "N3t Zer0").
_LEET_MAP = {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t"}
# Curated OCR confusables (dropped vowels etc.). Tiny on purpose — Production
# would back this with a Vietnamese spell-corrector. Keyed on lowercased token.
_OCR_CONFUSABLES = {"cng": "cong"}

# Heading detection: Roman numerals (I..X) or decimal section numbers
# ("1.", "2.3", "10.1.") followed by title text.
_HEADING_RE = re.compile(
    r"(?i)^((?:I{1,3}|IV|V|VI{0,3}|IX|X)\.|\d{1,2}\.|\d{1,2}\.\d{1,2}\.?)\s+.*$"
)

# Sentence boundary: end punctuation followed by whitespace. The lookbehind on
# whitespace means decimal/thousands figures like "1.500" (no space after the
# dot) are NOT split, while real sentence breaks are.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+")

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


def _deleet_token(token: str) -> str:
    """De-obfuscate leetspeak digits inside an *alphabetic* token.

    Applies only when the token mixes letters and digits and its letters are NOT
    all-uppercase — which protects chemical/unit formulas such as ``CO2``,
    ``H2O`` and pure numbers (``2023``, ``1500``) while fixing ``N3t`` → ``Net``
    and ``Zer0`` → ``Zero``.
    """
    letters = [c for c in token if c.isalpha()]
    has_digit = any(c.isdigit() for c in token)
    if not letters or not has_digit:
        return token
    if all(c.isupper() for c in letters):  # chemical formula (CO2, H2O, SO2)
        return token
    return "".join(_LEET_MAP.get(c, c) if c.isdigit() else c for c in token)


def _fix_confusable(token: str) -> str:
    """Replace a known OCR-confusable core word, preserving capitalisation."""
    prefix_len = len(token) - len(token.lstrip("([{\"'“‘"))
    suffix_len = len(token) - len(token.rstrip(".,;:!?)]}\"'”’"))
    core = token[prefix_len: len(token) - suffix_len] if suffix_len else token[prefix_len:]
    replacement = _OCR_CONFUSABLES.get(core.lower())
    if replacement is None:
        return token
    if core[:1].isupper():
        replacement = replacement.capitalize()
    return token[:prefix_len] + replacement + (token[len(token) - suffix_len:] if suffix_len else "")


def normalize_ocr_artifacts(text: str) -> str:
    """Repair common OCR/obfuscation noise before cleaning.

    Two passes per whitespace token: leetspeak de-obfuscation (``N3t Zer0`` →
    ``Net Zero``) and a curated confusable fix (``Cng`` → ``Cong``). Chemical
    figures (``CO2``, ``kWh``) and numbers are deliberately preserved.
    """
    out = []
    for token in text.split(" "):
        out.append(_fix_confusable(_deleet_token(token)))
    return " ".join(out)


def clean_text_advanced(raw_text: str, normalize_ocr: bool = False) -> list[str]:
    """Normalise and split raw text into clean, non-empty paragraphs.

    Steps: (optional) OCR-artifact repair; NFC-normalise; join intra-sentence
    line breaks while keeping paragraph breaks; drop page/footer noise (e.g.
    "Trang 12 / 100"). ``normalize_ocr`` is opt-in so the default path is byte-
    faithful to the input.
    """
    if normalize_ocr:
        raw_text = normalize_ocr_artifacts(raw_text)
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


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences on terminal punctuation (Vietnamese-safe)."""
    return [s for s in _SENTENCE_END_RE.split(text.strip()) if s]


def _hard_split_words(text: str, max_tokens: int) -> list[tuple[str, int]]:
    """Last resort: pack words greedily into <= max_tokens pieces.

    Used when a single sentence already exceeds the token bound (e.g. an
    un-punctuated run-on extracted from a PDF). Returns ``(text, count)`` pairs.
    """
    pieces: list[tuple[str, int]] = []
    buf: list[str] = []
    buf_tokens = 0
    for word in text.split():
        wt = count_tokens(word)
        if buf and buf_tokens + wt > max_tokens:
            pieces.append((" ".join(buf), buf_tokens))
            buf, buf_tokens = [word], wt
        else:
            buf.append(word)
            buf_tokens += wt
    if buf:
        pieces.append((" ".join(buf), buf_tokens))
    return pieces


def split_to_token_limit(text: str, max_tokens: int = MAX_TOKENS) -> list[tuple[str, int]]:
    """Split any text block into pieces that each fit within ``max_tokens``.

    Sentence-aware: sentences are packed greedily up to the bound; a single
    sentence still over the bound is hard-split on word boundaries. Text already
    within the bound is returned as a single ``(text, count)`` piece, so this is
    safe to call unconditionally. Empty/whitespace text yields ``[]``.
    """
    text = text.strip()
    if not text:
        return []
    pieces: list[tuple[str, int]] = []
    buf = ""
    buf_tokens = 0
    for sentence in _split_sentences(text):
        sent_tokens = count_tokens(sentence)
        if sent_tokens > max_tokens:
            if buf:
                pieces.append((buf, buf_tokens))
                buf, buf_tokens = "", 0
            pieces.extend(_hard_split_words(sentence, max_tokens))
        elif buf_tokens + sent_tokens > max_tokens:
            pieces.append((buf, buf_tokens))
            buf, buf_tokens = sentence, sent_tokens
        else:
            buf = f"{buf} {sentence}".strip()
            buf_tokens += sent_tokens
    if buf:
        pieces.append((buf, buf_tokens))
    return pieces


def chunk_document(
    paragraphs: list[str], initial_heading: str = "Thong tin chung"
) -> list[dict]:
    """Greedy semantic chunking bounded by MAX_TOKENS, heading-aware.

    A new heading flushes the current chunk and becomes the heading context for
    subsequent chunks. Within a section, paragraphs accumulate until adding the
    next one would exceed MAX_TOKENS, at which point the chunk is flushed.
    ``initial_heading`` seeds the heading context for text before the first
    heading (e.g. an upstream document-section label like "Bao cao Moi truong").

    Returns dicts of ``{"heading", "text", "token_count"}``.
    """
    chunks: list[dict] = []
    current_heading = initial_heading
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
            if para_tokens > MAX_TOKENS:
                # The paragraph alone overflows the window (e.g. a PDF block
                # with no blank-line breaks). Flush, split it sentence-aware,
                # emit the full pieces, and keep the last one in the buffer so
                # following short paragraphs can still pack into it.
                save_chunk()
                pieces = split_to_token_limit(para)
                for piece_text, piece_tokens in pieces[:-1]:
                    current_text, current_tokens = piece_text, piece_tokens
                    save_chunk()
                if pieces:
                    current_text, current_tokens = pieces[-1]
            elif current_tokens + para_tokens > MAX_TOKENS:
                save_chunk()
                current_text = para
                current_tokens = para_tokens
            else:
                current_text = f"{current_text} {para}".strip()
                current_tokens += para_tokens

    save_chunk()
    return chunks
