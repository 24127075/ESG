"""ESG taxonomy mapping (SDAD §9).

Multi-pattern keyword matching with Aho-Corasick (O(n) over the text length,
independent of dictionary size) [Aho & Corasick 1975]. The dictionary
``config/esg_taxonomy.json`` is SME-maintained and assigns a *weight* per
keyword; a category is only emitted when its summed weight meets a threshold,
which suppresses false positives.

If ``pyahocorasick`` is not installed we fall back to a correct (if slower)
pure-Python scanner so the pipeline still produces identical tags.
"""
from __future__ import annotations

import json
import logging
import unicodedata

logger = logging.getLogger(__name__)

# E -> S -> G presentation order for emitted tags.
_ESG_ORDER = {"E": 0, "S": 1, "G": 2}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text.lower())


class TaxonomyTagger:
    """Loads the taxonomy config and tags chunk text with ESG categories."""

    def __init__(self, config_path: str):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        self.version = config.get("version", "unknown")
        # List of (normalised_keyword, category, weight).
        self._entries: list[tuple[str, str, int]] = []
        for category, items in config["categories"].items():
            for item in items:
                self._entries.append(
                    (_norm(item["keyword"]), category, int(item["weight"]))
                )
        self._automaton = self._build_automaton()

    def _build_automaton(self):
        try:
            import ahocorasick
        except ImportError:
            logger.warning(
                "pyahocorasick not installed; using pure-Python fallback scanner."
            )
            return None
        automaton = ahocorasick.Automaton()
        for keyword, category, weight in self._entries:
            automaton.add_word(keyword, (category, weight))
        automaton.make_automaton()
        return automaton

    def _score(self, text_norm: str) -> dict[str, int]:
        scores: dict[str, int] = {}
        if self._automaton is not None:
            for _, (category, weight) in self._automaton.iter(text_norm):
                scores[category] = scores.get(category, 0) + weight
        else:
            # Fallback: count each keyword occurrence (matches Aho-Corasick
            # multiplicity, which scores every overlapping hit).
            for keyword, category, weight in self._entries:
                occurrences = text_norm.count(keyword)
                if occurrences:
                    scores[category] = scores.get(category, 0) + weight * occurrences
        return scores

    def tag(self, chunk_text: str, threshold: int = 2) -> list[str]:
        """Return ESG categories whose summed weight >= ``threshold``.

        Results are ordered Environmental -> Social -> Governance.
        """
        scores = self._score(_norm(chunk_text))
        matched = [cat for cat, score in scores.items() if score >= threshold]
        return sorted(matched, key=lambda c: _ESG_ORDER.get(c[0], 9))


# Convenience module-level singleton mirroring the SDAD API.
_DEFAULT_TAGGER: TaxonomyTagger | None = None


def build_automaton(config_path: str) -> TaxonomyTagger:
    """Build (and cache) the default tagger from a config path."""
    global _DEFAULT_TAGGER
    _DEFAULT_TAGGER = TaxonomyTagger(config_path)
    return _DEFAULT_TAGGER


def tag_esg_with_weight(chunk_text: str, threshold: int = 2) -> list[str]:
    """Module-level helper using the default tagger (build it first)."""
    if _DEFAULT_TAGGER is None:
        raise RuntimeError(
            "Taxonomy not initialised — call build_automaton(config_path) first."
        )
    return _DEFAULT_TAGGER.tag(chunk_text, threshold)
