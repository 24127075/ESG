"""Quality metrics & SLA gates (SDAD §11).

Implements the benchmark measurements Phase 2 must pass before it is considered
Production-ready:

    | Metric        | Method                                   | SLA               |
    |---------------|------------------------------------------|-------------------|
    | OCR CER       | Character Error Rate over 100 scan pages | < 5%              |
    | Table Ext.    | Correct table extraction ratio           | > 85%             |
    | Boundary Err. | Mis-split sentence ratio in chunking     | < 1%              |
    | Taxonomy FP   | False-positive ratio in ESG tagging      | < 10%             |
    | Throughput    | Aho-Corasick + chunking on 1 CPU core    | > 500 pages / sec |

NOTE on the throughput SLA (see docs/DANH_GIA_SDA.md issue #4). The SDAD's
">500 pages/sec" target is scoped to the *Aho-Corasick + chunking* micro-step
only, and on reference hardware that step measures ~600 pages/sec (taxonomy scan
alone ~40k pages/sec), so the target IS met. It is, however, misleading as an
*end-to-end* Phase 2 figure: the real bottleneck is extraction (Camelot ~1-5
pages/sec) and especially OCR (~0.1-1 pages/sec). We therefore keep the micro
SLA (``throughput_pps``) and add an honest end-to-end SLA (``throughput_e2e_pps``)
bounded by extraction/OCR.
"""
from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass


# ── Edit distance (for CER) ──────────────────────────────────────────────────
def levenshtein(a: str, b: str) -> int:
    """Classic Wagner-Fischer edit distance (substitutions/insertions/deletions)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(
                min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
            )
        prev = curr
    return prev[-1]


def character_error_rate(hypothesis: str, reference: str) -> float:
    """OCR CER = edit_distance(hyp, ref) / len(ref). NFC-normalised. (< 5% SLA)."""
    hyp = unicodedata.normalize("NFC", hypothesis)
    ref = unicodedata.normalize("NFC", reference)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(hyp, ref) / len(ref)


def corpus_cer(pairs: list[tuple[str, str]]) -> float:
    """Aggregate CER over (hypothesis, reference) pairs, weighted by ref length."""
    total_edits = 0
    total_chars = 0
    for hyp, ref in pairs:
        ref_n = unicodedata.normalize("NFC", ref)
        total_edits += levenshtein(unicodedata.normalize("NFC", hyp), ref_n)
        total_chars += len(ref_n)
    return (total_edits / total_chars) if total_chars else 0.0


# ── Ratio metrics ────────────────────────────────────────────────────────────
def table_extraction_accuracy(correct_tables: int, total_tables: int) -> float:
    """Share of tables extracted without merging columns/rows (> 85% SLA)."""
    return (correct_tables / total_tables) if total_tables else 0.0


def boundary_error_rate(mis_split: int, total_sentences: int) -> float:
    """Share of sentences split at the wrong point during chunking (< 1% SLA)."""
    return (mis_split / total_sentences) if total_sentences else 0.0


def taxonomy_false_positive_rate(false_positives: int, total_tags: int) -> float:
    """Share of ESG tags that are false positives (< 10% SLA)."""
    return (false_positives / total_tags) if total_tags else 0.0


def measure_throughput(process_fn, pages: list, *, repeat: int = 1) -> float:
    """Pages/second for ``process_fn`` over ``pages``.

    ``process_fn`` is called once per page; ``repeat`` re-runs the whole set to
    smooth out timing noise on tiny inputs. Use it both for the AC+chunking micro
    benchmark (``throughput_pps``) and the end-to-end figure (``throughput_e2e_pps``)
    by passing the relevant callable.
    """
    if not pages:
        return 0.0
    start = time.perf_counter()
    for _ in range(repeat):
        for page in pages:
            process_fn(page)
    elapsed = time.perf_counter() - start
    processed = len(pages) * repeat
    return processed / elapsed if elapsed > 0 else float("inf")


def measure_taxonomy_throughput(tagger, pages: list[str], *, repeat: int = 1) -> float:
    """Pages/sec for the pure Aho-Corasick taxonomy scan (``tagger.tag``)."""
    return measure_throughput(tagger.tag, pages, repeat=repeat)


# ── SLA gate ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SLA:
    name: str
    threshold: float
    comparison: str  # "<" means lower-is-better, ">" means higher-is-better

    def passed(self, value: float) -> bool:
        return value < self.threshold if self.comparison == "<" else value > self.threshold


SLA_TARGETS: dict[str, SLA] = {
    "ocr_cer": SLA("OCR CER", 0.05, "<"),
    "table_extraction": SLA("Table Extraction", 0.85, ">"),
    "boundary_error": SLA("Boundary Error", 0.01, "<"),
    "taxonomy_fp": SLA("Taxonomy FP", 0.10, "<"),
    # SDAD's micro benchmark: Aho-Corasick + chunking only (achievable, ~600 pps).
    "throughput_pps": SLA("Throughput AC+chunking (pages/sec)", 500.0, ">"),
    # Honest end-to-end Phase 2 target — bounded by extraction/OCR, NOT in the
    # original SDAD. 3 pages/sec/core is a realistic text-based-PDF floor.
    "throughput_e2e_pps": SLA("Throughput end-to-end (pages/sec)", 3.0, ">"),
}


def evaluate_slas(measurements: dict[str, float]) -> dict[str, bool]:
    """Map each measured metric to pass/fail against its SLA target."""
    results: dict[str, bool] = {}
    for key, value in measurements.items():
        sla = SLA_TARGETS.get(key)
        if sla is not None:
            results[key] = sla.passed(value)
    return results
