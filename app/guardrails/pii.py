"""PII detection and reversible masking with Presidio (§B10.2 L3 / L7, §B10.4).

Values become numbered placeholders (`jane@acme.com` -> `<EMAIL_ADDRESS_1>`); the same value always gets the
same placeholder within a request. `PIIMap` keeps placeholder -> original and remembers which values the user
supplied, because only those may be unmasked in the answer (§B10.4).

Two additions to plain Presidio (D-023):
- a custom `EMPLOYEE_ID` recogniser (`EMP-#####`) and a US phone pattern, because Presidio's phone recogniser
  misses a bare "(463) 697-8333" without a context word;
- PERSON hits on directory names are not masked: employees (the graph and the agent answer "who manages X?"
  with them) and system / team / product names that spaCy tags as people ("Kafka Cluster").
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

from app.retrieval.router import TABLES, load_known_entities

INPUT_ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN", "IBAN_CODE",
                  "IP_ADDRESS", "EMPLOYEE_ID"]
OUTPUT_ENTITIES = INPUT_ENTITIES  # PERSON too: customer names from the corpus stay masked (D-023)
SCORE_MIN = 0.4
PLACEHOLDER = re.compile(r"<([A-Z_]+)_(\d+)>")


@dataclass
class PIIMap:
    """placeholder -> original value, for one request. Never persisted or traced (§B10.4)."""
    values: dict[str, str] = field(default_factory=dict)
    user: set[str] = field(default_factory=set)  # placeholders whose value came from the user's message

    def placeholder(self, entity: str, value: str, *, from_user: bool) -> str:
        for ph, v in self.values.items():
            if v == value and ph.startswith(f"<{entity}_"):
                break
        else:
            n = sum(ph.startswith(f"<{entity}_") for ph in self.values) + 1
            ph = f"<{entity}_{n}>"
            self.values[ph] = value
        if from_user:
            self.user.add(ph)
        return ph

    def unmask_user(self, text: str) -> str:
        """Restore only the values the user supplied in this turn; corpus and tool PII stays masked."""
        return PLACEHOLDER.sub(lambda m: self.values[m.group(0)] if m.group(0) in self.user else m.group(0),
                               text)


def load_directory(tables: Path = TABLES) -> set[str]:
    """Lower-cased names that are not PII inside Acme: employees, systems, teams, products."""
    names = set(load_known_entities(tables))
    path = tables / "employees.csv"
    if path.exists():
        with open(path, newline="") as f:
            names |= {r["name"] for r in csv.DictReader(f)}
    return {n.lower() for n in names}


class PIIMasker:
    def __init__(self, directory: set[str] | None = None):
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}]})
        self.analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
        self.analyzer.registry.add_recognizer(PatternRecognizer(
            supported_entity="EMPLOYEE_ID", patterns=[Pattern("employee_id", r"\bEMP-\d{5}\b", 0.9)]))
        self.analyzer.registry.add_recognizer(PatternRecognizer(
            supported_entity="PHONE_NUMBER",
            patterns=[Pattern("us_phone", r"(?<![\w.])(?:\+1[\s.-]?)?\(\d{3}\)\s?\d{3}[\s.-]\d{4}(?![\w.])",
                              0.7),
                      Pattern("us_phone_dashed", r"(?<![\w.-])\d{3}-\d{3}-\d{4}(?![\w-])", 0.6)]))
        self.directory = load_directory() if directory is None else directory

    def _keep(self, text: str, r: RecognizerResult) -> bool:
        if r.entity_type != "PERSON":
            return True
        words = text[r.start:r.end].strip().lower().split()
        return not any(words and all(w in n.split() for w in words) for n in self.directory)

    def analyze(self, text: str, entities: list[str]) -> list[RecognizerResult]:
        """Non-overlapping hits, highest score (then longest) first. Pure: safe to run in a thread."""
        if not text.strip():
            return []
        hits = [r for r in self.analyzer.analyze(text, language="en", entities=entities,
                                                 score_threshold=SCORE_MIN) if self._keep(text, r)]
        kept: list[RecognizerResult] = []
        for r in sorted(hits, key=lambda r: (-r.score, -(r.end - r.start))):
            if all(r.end <= k.start or r.start >= k.end for k in kept):
                kept.append(r)
        return sorted(kept, key=lambda r: r.start)

    @staticmethod
    def apply(text: str, hits: list[RecognizerResult], pii: PIIMap, *,
              from_user: bool) -> tuple[str, list[str]]:
        """Replace the hits with placeholders (mutates `pii`). Returns (masked text, entity types masked)."""
        out, last, types = [], 0, []
        for r in hits:
            value = text[r.start:r.end]
            if PLACEHOLDER.fullmatch(value):
                continue
            out += [text[last:r.start], pii.placeholder(r.entity_type, value, from_user=from_user)]
            last = r.end
            types.append(r.entity_type)
        out.append(text[last:])
        return "".join(out), types

    def mask(self, text: str, pii: PIIMap, *, entities: list[str] = INPUT_ENTITIES,
             from_user: bool = False) -> tuple[str, list[str]]:
        return self.apply(text, self.analyze(text, entities), pii, from_user=from_user)


@lru_cache
def get_masker() -> PIIMasker:
    return PIIMasker()
