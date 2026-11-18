"""Prepare a selected part of NLM's UMLS MRCONSO.RRF for the local lexicon.

Set `mrconso` to the RRF file (optionally gzip-compressed), and `sources` to a
nonempty list of permitted source abbreviations. `language` defaults to ENG.
An optional `ttys` list restricts accepted term types. Pair `mrsty` with
`semantic_types` to select CUIs with at least one of the listed TUIs.
Use the bundle's
`source_aliases` to reconcile the original SAB on each name with native sources.
"""

from __future__ import annotations

import gzip
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def _allowed(config: Mapping[str, Any], key: str) -> set[str]:
    values = config.get(key)
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(value, str) and value.strip() for value in values)
    ):
        raise ValueError(f"UMLS {key} must be a nonempty list of strings")
    return {value.strip() for value in values}


def _native_key(sab: str, tty: str, code: str) -> str | None:
    # For MeSH an entry term can name a narrower concept within the record.
    # Only the record's own heading or SCR name asserts its D/C identity.
    if sab == "MSH":
        if (tty == "MH" and re.fullmatch(r"D[0-9]+", code)) or (
            tty == "NM" and re.fullmatch(r"C[0-9]+", code)
        ):
            return "MeSH"
        return None
    if sab == "RXNORM" and re.fullmatch(r"[1-9][0-9]*", code):
        return "RxNorm"
    if sab == "NCI" and re.fullmatch(r"C[0-9]+", code):
        return "NCIt"
    if sab == "SNOMEDCT_US" and re.fullmatch(r"[1-9][0-9]{5,17}", code):
        return "SNOMED"
    if sab == "DRUGBANK" and re.fullmatch(r"DB[0-9]+", code):
        return "DrugBank"
    return None


def _rrf(path: Path, columns: int) -> Iterator[tuple[int, list[str]]]:
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            fields = line.rstrip("\r\n").split("|")
            if len(fields) != columns + 1 or fields[-1]:
                raise ValueError(f"{path}:{number}: expected {columns} fields and a final pipe")
            yield number, fields[:-1]


def _atoms(
    path: Path, sources: set[str], language: str, ttys: set[str] | None
) -> Iterator[tuple[str, str, str, str, int, str | None, str, bool]]:
    for number, fields in _rrf(path, 18):
        cui, lat, ts, _, stt, _, ispref, _, _, _, _, sab, tty, code, name, _, suppress, _ = fields
        if lat != language or sab not in sources or suppress != "N":
            continue
        kept = ttys is None or tty in ttys
        key = _native_key(sab, tty, code)
        # An atom outside the TTY filter still counts toward code cardinality.
        if not kept and key is None:
            continue
        if not cui or not tty or (kept and not name.strip()):
            raise ValueError(f"{path}:{number}: missing UMLS CUI, name or term type")
        # P/PF/Y identifies the preferred atom. A filtered subset can
        # lack that atom; rank retained names deterministically in that case.
        rank = 4 * (ts != "P") + 2 * (stt != "PF") + (ispref != "Y")
        yield cui, name.strip(), sab, tty, rank, key, code, kept


def read_umls(config: Mapping[str, Any], sink: Any) -> None:
    """Emit one concept per retained CUI, with source-attributed names."""
    sources = _allowed(config, "sources")
    ttys = _allowed(config, "ttys") if "ttys" in config else None
    if ("mrsty" in config) != ("semantic_types" in config):
        raise ValueError("UMLS semantic type filtering needs both mrsty and semantic_types")
    language = config.get("language", "ENG")
    if not isinstance(language, str) or not re.fullmatch("[A-Z]{3}", language):
        raise ValueError("UMLS language must be a three-letter MRCONSO LAT value such as ENG")
    path = Path(sink.path(config["mrconso"]))
    # Source subsets and release files need not arrive in CUI order. Spill the
    # eligible atoms and sort on disk so release size does not bound available RAM.
    with (
        TemporaryDirectory(dir=sink.scratch) as directory,
        closing(sqlite3.connect(Path(directory) / "atoms.sqlite")) as db,
    ):
        db.execute("PRAGMA temp_store=FILE")
        # Sort spill files otherwise go to the system temp directory, whatever
        # the database location. The setting is process-wide, so restore it.
        db.execute(f"PRAGMA temp_store_directory = '{directory.replace(chr(39), chr(39) * 2)}'")
        try:
            _read_atoms(config, sink, db, path, sources, language, ttys)
        finally:
            db.execute("PRAGMA temp_store_directory = ''")


def _read_atoms(
    config: Mapping[str, Any],
    sink: Any,
    db: sqlite3.Connection,
    path: Path,
    sources: set[str],
    language: str,
    ttys: set[str] | None,
) -> None:
    vocabulary = config["vocabulary_id"]
    db.execute(
        "CREATE TABLE atoms(cui TEXT, name TEXT, sab TEXT, tty TEXT, "
        "rank INTEGER, key_type TEXT, code TEXT, kept INTEGER)"
    )
    insert = "INSERT INTO atoms SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8"
    if "mrsty" in config:
        types = _allowed(config, "semantic_types")
        if not all(re.fullmatch(r"T[0-9]{3}", value) for value in types):
            raise ValueError("UMLS semantic_types must contain TUIs such as T121")
        db.execute("CREATE TABLE eligible(cui TEXT PRIMARY KEY) WITHOUT ROWID")
        db.executemany(
            "INSERT OR IGNORE INTO eligible VALUES (?)",
            ((row[0],) for _, row in _rrf(Path(sink.path(config["mrsty"])), 6) if row[1] in types),
        )
        insert += " AND ?1 IN (SELECT cui FROM eligible)"
    db.executemany(insert, _atoms(path, sources, language, ttys))
    previous = None
    for cui, name, sab, tty in db.execute(
        "SELECT cui, name, sab, tty FROM atoms WHERE kept ORDER BY cui, rank, name, sab, tty"
    ):
        if cui != previous:
            sink.add(
                "concepts",
                vocabulary_id=vocabulary,
                concept_code=cui,
                concept_name=name,
                concept_class="UMLS concept",
                name_source=sab,
            )
            sink.add(
                "xrefs",
                vocabulary_id=vocabulary,
                concept_code=cui,
                key_type="UMLS_CUI",
                key_value=cui,
                assertion_source="UMLS",
            )
            previous = cui
        sink.add(
            "synonyms",
            vocabulary_id=vocabulary,
            concept_code=cui,
            synonym=name,
            synonym_type=tty,
            source=sab,
        )
    if previous is None:
        raise ValueError(f"{path}: no unsuppressed UMLS atoms match the requested filters")

    # UMLS can collapse several source codes to one CUI, or split one
    # source code across CUIs. Neither establishes a unique identity.
    rows = db.execute(
        "WITH pairs AS (SELECT cui, key_type, code, max(kept) AS kept FROM atoms "
        "WHERE key_type IS NOT NULL GROUP BY cui, key_type, code), counted AS (SELECT *, "
        "count(*) OVER (PARTITION BY cui, key_type) AS codes_per_cui, "
        "count(*) OVER (PARTITION BY key_type, code) AS cuis_per_code FROM pairs) "
        "SELECT cui, key_type, code FROM counted WHERE codes_per_cui=1 AND cuis_per_code=1 "
        "AND kept ORDER BY cui, key_type"
    )
    for cui, key_type, code in rows:
        sink.add(
            "xrefs",
            vocabulary_id=vocabulary,
            concept_code=cui,
            key_type=key_type,
            key_value=code,
            assertion_source="UMLS",
        )
