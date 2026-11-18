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
    """Read FDA UNIIs.zip or its UNII_Names_*.txt, sorted by name, not by UNII."""
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


def _drugbank_record(drug: ET.Element, vocabulary: str, sink: Any) -> bool:
    """Add one drug; return True when its direct UNII was malformed and dropped."""
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
    # Nested salts and products have independent identities, including their own UNIIs.
    unii = drug.findtext("{*}unii", default="").strip()
    if not unii:
        return False
    if not re.fullmatch(r"[A-Z0-9]{10}", unii):
        return True
    sink.add(
        "xrefs",
        vocabulary_id=vocabulary,
        concept_code=code,
        key_type="UNII",
        key_value=unii,
        assertion_source=vocabulary,
    )
    return False


def read_drugbank(config: Mapping[str, Any], sink: Any) -> None:
    """Stream top-level drug records from the DrugBank full XML database."""
    path = sink.path(config["path"])
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
                    skipped += _drugbank_record(node, config["vocabulary_id"], sink)
                    count += 1
                root.remove(node)
            depth -= 1
    if count == 0:
        raise ValueError(f"{path}: DrugBank XML contains no drug records")
    if skipped:
        print(f"{path}: skipped {skipped} invalid UNII value(s)", file=sys.stderr)
