"""Read local DrugBank full XML and the FDA GSRS UNII Names release."""

from __future__ import annotations

import csv
import io
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from fnmatch import fnmatch
from pathlib import Path
from typing import IO, Any
from zipfile import ZipFile


@contextmanager
def _release_file(path: Path, pattern: str) -> Iterator[IO[bytes]]:
    """Stream a plain release file or the single matching member of its archive."""
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            members = [
                item
                for item in archive.infolist()
                if not item.is_dir()
                and "__MACOSX" not in Path(item.filename).parts
                and not Path(item.filename).name.startswith("._")
                and fnmatch(Path(item.filename).name.lower(), pattern)
            ]
            if len(members) != 1:
                raise ValueError(f"{path}: expected one {pattern} member, found {len(members)}")
            with archive.open(members[0]) as stream:
                yield stream
    else:
        with path.open("rb") as stream:
            yield stream


def read_gsrs(config: Mapping[str, Any], sink: Any) -> None:
    """Read FDA UNIIs.zip or its UNII_Names_*.txt, sorted by name, not by UNII.

    Optional `records` points to UNII_Data.zip or its UNII_Records_*.txt and adds
    each substance's RxNorm, NCIt and PubChem codes as keys.
    """
    vocabulary = config["vocabulary_id"]
    path = sink.path(config["path"])
    display_names: dict[str, str] = {}
    with (
        _release_file(path, "unii*names*.txt") as stream,
        io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text,
    ):
        # Quotes in substance names are literal characters in FDA's TSV.
        rows = csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        fields = {"NAME", "TYPE", "UNII", "DISPLAY_NAME"}
        if not fields.issubset(rows.fieldnames or []):
            raise ValueError(f"{path}: expected FDA UNII Names columns {sorted(fields)}")
        for row in rows:
            code = (row["UNII"] or "").strip()
            display = (row["DISPLAY_NAME"] or "").strip()
            if not re.fullmatch(r"[A-Z0-9]{10}", code) or not display:
                raise ValueError(f"{path}:{rows.line_num}: invalid UNII or missing DISPLAY_NAME")
            if code not in display_names:
                display_names[code] = display
                sink.add(
                    "concepts",
                    vocabulary_id=vocabulary,
                    concept_code=code,
                    concept_name=display,
                    concept_class="substance",
                    name_source=vocabulary,
                )
            elif display_names[code] != display:
                raise ValueError(f"{path}:{rows.line_num}: conflicting DISPLAY_NAME for {code}")

            # Brand/trade, code, and unclassified names are ineligible for substance identity.
            kind = (row["TYPE"] or "").strip().lower()
            name = (row["NAME"] or "").strip()
            if kind in {"of", "sys", "cn"} and name and name != display:
                sink.add(
                    "synonyms",
                    vocabulary_id=vocabulary,
                    concept_code=code,
                    synonym=name,
                    synonym_type=kind,
                    source=vocabulary,
                )
    if not display_names:
        raise ValueError(f"{path}: FDA UNII Names file contains no substances")
    if "records" in config:
        _unii_records(sink.path(config["records"]), vocabulary, display_names, sink)


# UNII Records columns that name the same substance in another namespace.
_RECORD_KEYS = {"RXCUI": "RxNorm", "NCIT": "NCIt", "PUBCHEM": "PubChem CID"}
_RECORD_FORMATS = {"RXCUI": r"[1-9][0-9]*", "NCIT": r"C[0-9]+", "PUBCHEM": r"[1-9][0-9]*"}


def _unii_records(path: Path, vocabulary: str, known: Mapping[str, str], sink: Any) -> None:
    """Add FDA's RxNorm, NCIt and PubChem codes for the substances already read."""
    skipped = 0
    with (
        _release_file(path, "unii*records*.txt") as stream,
        io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text,
    ):
        rows = csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        fields = {"UNII", *_RECORD_KEYS}
        if not fields.issubset(rows.fieldnames or []):
            raise ValueError(f"{path}: expected FDA UNII Records columns {sorted(fields)}")
        for row in rows:
            code = (row["UNII"] or "").strip()
            if code not in known:
                continue
            for column, key_type in _RECORD_KEYS.items():
                value = (row[column] or "").strip()
                if not value:
                    continue
                if not re.fullmatch(_RECORD_FORMATS[column], value):
                    skipped += 1
                    continue
                sink.add(
                    "xrefs",
                    vocabulary_id=vocabulary,
                    concept_code=code,
                    key_type=key_type,
                    key_value=value,
                    assertion_source="FDA UNII Records",
                )
    if skipped:
        print(f"{path}: skipped {skipped} malformed cross-reference value(s)", file=sys.stderr)


# DrugBank external-identifier resources and the key namespace each becomes.
# RxCUI uses the RxNorm vocabulary ID so it joins RxNorm concepts directly.
DEFAULT_EXTERNAL_IDENTIFIERS = {
    "RxCUI": "RxNorm",
    "ChEBI": "ChEBI",
    "PubChem Compound": "PubChem CID",
    "PubChem Substance": "PubChem SID",
    "ChEMBL": "ChEMBL",
    "KEGG Drug": "KEGG Drug",
    "KEGG Compound": "KEGG Compound",
    "PharmGKB": "PharmGKB",
    "UniProtKB": "UniProtKB",
}
_IDENTIFIER_FORMATS = {"RxNorm": r"[1-9][0-9]*"}


def _unii_key(owner: str, unii: str, vocabulary: str, sink: Any) -> bool:
    """Add a UNII key; return True when the value is malformed and dropped."""
    if not unii:
        return False
    if not re.fullmatch(r"[A-Z0-9]{10}", unii):
        return True
    sink.add(
        "xrefs",
        vocabulary_id=vocabulary,
        concept_code=owner,
        key_type="UNII",
        key_value=unii,
        assertion_source=vocabulary,
    )
    return False


def _drugbank_record(
    drug: ET.Element,
    vocabulary: str,
    sink: Any,
    external: Mapping[str, str],
    salts: bool,
    seen_salts: set[str],
) -> int:
    """Add one drug, its salts and identifiers; return the number of malformed values dropped."""
    primary = [
        (node.text or "").strip()
        for node in drug.findall("{*}drugbank-id")
        if node.get("primary") in {"true", "1"}
    ]
    name = drug.findtext("{*}name", default="").strip()
    if len(primary) != 1 or not primary[0] or not name:
        raise ValueError("DrugBank drug record needs one primary drugbank-id and a name")
    code = primary[0]
    sink.add(
        "concepts",
        vocabulary_id=vocabulary,
        concept_code=code,
        concept_name=name,
        concept_class=drug.get("type") or "substance",
        name_source=vocabulary,
    )
    for synonym in drug.findall("{*}synonyms/{*}synonym"):
        alias = (synonym.text or "").strip()
        language = (synonym.get("language") or "").strip().casefold()
        if alias and alias != name and language in {"", "english", "en"}:
            sink.add(
                "synonyms",
                vocabulary_id=vocabulary,
                concept_code=code,
                synonym=alias,
                synonym_type="synonym",
                source=vocabulary,
            )
    dropped = int(_unii_key(code, drug.findtext("{*}unii", default="").strip(), vocabulary, sink))
    for identifier in drug.findall("{*}external-identifiers/{*}external-identifier"):
        key_type = external.get((identifier.findtext("{*}resource") or "").strip())
        value = (identifier.findtext("{*}identifier") or "").strip()
        if key_type is None or not value:
            continue
        if not re.fullmatch(_IDENTIFIER_FORMATS.get(key_type, r"\S+"), value):
            dropped += 1
            continue
        sink.add(
            "xrefs",
            vocabulary_id=vocabulary,
            concept_code=code,
            key_type=key_type,
            key_value=value,
            assertion_source=vocabulary,
        )
    if not salts:
        return dropped
    # Nested salts have independent identities, including their own UNIIs.
    for salt in drug.findall("{*}salts/{*}salt"):
        salt_code = salt.findtext("{*}drugbank-id", default="").strip()
        salt_name = salt.findtext("{*}name", default="").strip()
        if not salt_code or not salt_name or salt_code in seen_salts:
            continue
        seen_salts.add(salt_code)
        sink.add(
            "concepts",
            vocabulary_id=vocabulary,
            concept_code=salt_code,
            concept_name=salt_name,
            concept_class="salt",
            name_source=vocabulary,
        )
        sink.add(
            "relationships",
            vocabulary_id_1=vocabulary,
            concept_code_1=salt_code,
            predicate="salt_of",
            vocabulary_id_2=vocabulary,
            concept_code_2=code,
            assertion_source=vocabulary,
        )
        dropped += _unii_key(
            salt_code, salt.findtext("{*}unii", default="").strip(), vocabulary, sink
        )
    return dropped


def read_drugbank(config: Mapping[str, Any], sink: Any) -> None:
    """Stream top-level drug records from the DrugBank full XML database."""
    path = sink.path(config["path"])
    external = config.get("external_identifiers", DEFAULT_EXTERNAL_IDENTIFIERS)
    if not isinstance(external, dict) or not all(
        isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
        for k, v in external.items()
    ):
        raise ValueError("DrugBank external_identifiers must map resource names to key namespaces")
    salts = config.get("salts", True)
    if not isinstance(salts, bool):
        raise ValueError("DrugBank salts must be true or false")
    seen_salts: set[str] = set()
    count = skipped = 0
    with _release_file(path, "*.xml") as stream:
        events = ET.iterparse(stream, events=("start", "end"))
        _, root = next(events)
        if root.tag.rsplit("}", 1)[-1] != "drugbank":
            raise ValueError(f"{path}: expected a DrugBank full XML database")
        depth = 1
        for event, node in events:
            if event == "start":
                depth += 1
                continue
            if depth == 2:
                if node.tag.rsplit("}", 1)[-1] == "drug":
                    skipped += _drugbank_record(
                        node, config["vocabulary_id"], sink, external, salts, seen_salts
                    )
                    count += 1
                root.remove(node)
            depth -= 1
    if count == 0:
        raise ValueError(f"{path}: DrugBank XML contains no drug records")
    if skipped:
        print(f"{path}: skipped {skipped} invalid UNII or identifier value(s)", file=sys.stderr)
