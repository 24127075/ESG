"""SLA metrics tests (SDAD §11) — offline."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from esg_pipeline.phase2_preprocessing.metrics import (  # noqa: E402
    boundary_error_rate,
    character_error_rate,
    corpus_cer,
    evaluate_slas,
    levenshtein,
    measure_throughput,
    table_extraction_accuracy,
    taxonomy_false_positive_rate,
)


def test_levenshtein_and_cer():
    assert levenshtein("kitten", "sitting") == 3
    assert character_error_rate("abc", "abc") == 0.0
    # one substitution over 3 reference chars
    assert abs(character_error_rate("abx", "abc") - (1 / 3)) < 1e-9


def test_corpus_cer_weighted():
    pairs = [("abc", "abc"), ("abx", "abc")]
    assert abs(corpus_cer(pairs) - (1 / 6)) < 1e-9


def test_ratio_metrics():
    assert table_extraction_accuracy(90, 100) == 0.9
    assert boundary_error_rate(0, 500) == 0.0
    assert taxonomy_false_positive_rate(5, 100) == 0.05


def test_throughput_positive():
    pps = measure_throughput(lambda p: p.upper(), ["a"] * 50, repeat=20)
    assert pps > 0


def test_sla_evaluation_pass_and_fail():
    results = evaluate_slas(
        {
            "ocr_cer": 0.03,           # < 5%  -> pass
            "table_extraction": 0.90,  # > 85% -> pass
            "boundary_error": 0.02,    # < 1%  -> FAIL
            "taxonomy_fp": 0.08,       # < 10% -> pass
            "throughput_pps": 600.0,   # > 500 -> pass
        }
    )
    assert results == {
        "ocr_cer": True,
        "table_extraction": True,
        "boundary_error": False,
        "taxonomy_fp": True,
        "throughput_pps": True,
    }
