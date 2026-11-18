"""Portable file schemas for the MPRINT drug dictionary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import yaml


@dataclass(frozen=True)
class TableSpec:
    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    mandatory: bool = False

    @property
    def columns(self) -> tuple[str, ...]:
        return self.required + self.optional


CONCEPTS = TableSpec(
    "concepts",
    ("vocabulary_id", "concept_code", "concept_name"),
    ("concept_class", "status", "valid_start_date", "valid_end_date", "replaced_by", "name_source"),
    mandatory=True,
)
SYNONYMS = TableSpec(
    "synonyms", ("vocabulary_id", "concept_code", "synonym"), ("synonym_type", "source")
)
RELATIONSHIPS = TableSpec(
    "relationships",
    ("vocabulary_id_1", "concept_code_1", "predicate", "vocabulary_id_2", "concept_code_2"),
)
XREFS = TableSpec(
    "xrefs", ("vocabulary_id", "concept_code", "key_type", "key_value"), ("assertion_source",)
)
PRODUCT_EVIDENCE = TableSpec(
    "product_evidence",
    ("vocabulary_id", "concept_code", "product_name"),
    (
        "application_type",
        "application_number",
        "product_number",
        "ingredient_text",
        "strength",
        "dose_form",
        "route",
        "country",
        "holder",
    ),
)
TABLES = {s.name: s for s in (CONCEPTS, SYNONYMS, RELATIONSHIPS, XREFS)}
OUTPUTS = {**TABLES, PRODUCT_EVIDENCE.name: PRODUCT_EVIDENCE}
DUCKDB_CONFIG = {"autoinstall_known_extensions": False, "autoload_known_extensions": False}
CONCEPT_STATUSES = (
    "active",
    "not_current",
    "remapped",
    "quantified",
    "obsolete",
    "never_active",
    "unknown",
)


def read_table_csv(con: duckdb.DuckDBPyConnection, path: Path, spec: TableSpec):
    """Read a prepared table in the exact dialect TableSink and publish_tables write."""
    return con.read_csv(
        str(path),
        columns=dict.fromkeys(spec.columns, "VARCHAR"),
        header=True,
        delimiter=",",
        quotechar='"',
        escapechar='"',
        auto_detect=False,
    )


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def validate_aliases(aliases):
    if not isinstance(aliases, dict):
        raise ValueError("source_aliases must map source labels to vocabulary names")
    return {_text(k, "source alias"): _text(v, "source alias value") for k, v in aliases.items()}


def validate_declaration(payload):
    """Check and normalize vocabulary metadata supplied by native and tabular inputs."""
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("vocabularies"), list)
        or not payload["vocabularies"]
    ):
        raise ValueError("the declaration requires a nonempty 'vocabularies' list")
    items = []
    seen = set()
    for entry in payload["vocabularies"]:
        if not isinstance(entry, dict):
            raise ValueError("each vocabulary declaration must be a mapping")
        vocab = _text(entry.get("vocabulary_id"), "vocabulary_id")
        if vocab != vocab.strip() or vocab.casefold() in seen:
            raise ValueError(f"duplicate or whitespace-padded vocabulary ID {vocab!r}")
        seen.add(vocab.casefold())
        item = {
            "vocabulary_id": vocab,
            "release": _text(entry.get("release"), f"{vocab} release"),
            "license": _text(entry.get("license", "unspecified"), f"{vocab} license"),
        }
        if entry.get("description") is not None:
            item["description"] = _text(entry["description"], f"{vocab} description")
        items.append(item)
    return {
        "vocabularies": items,
        "source_aliases": validate_aliases(payload.get("source_aliases", {})),
    }


def load_declaration(path: Path):
    try:
        return validate_declaration(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
