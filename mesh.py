"""Read full NLM MeSH descriptor and supplementary concept record XML files.

Configure `descriptors` and/or `supplemental` with paths to release XMLs or
their gzip-compressed copies. Only terms of the record's preferred concept
name that record's identity. Other concepts, indexing headings and
pharmacological actions have distinct meanings. Optional `descriptor_tree_prefixes`
and `scr_classes` select records by their native MeSH classifications.
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def _record(record: ET.Element, vocabulary: str, path: Path, sink: Any) -> None:
    descriptor = record.tag == "DescriptorRecord"
    prefix = "Descriptor" if descriptor else "SupplementalRecord"
    code = (record.findtext(f"{prefix}UI") or "").strip()
    label = (record.findtext(f"{prefix}Name/String") or "").strip()
    if not code or not label:
        raise ValueError(f"{path}: {record.tag} missing its UI or name")
    sink.add(
        "concepts",
        vocabulary_id=vocabulary,
        concept_code=code,
        concept_name=label,
        concept_class="MeSH Descriptor" if descriptor else "MeSH SCR",
        name_source="MeSH",
    )
    preferred = record.findall("ConceptList/Concept[@PreferredConceptYN='Y']")
    if len(preferred) != 1:
        raise ValueError(f"{path}: {code} must have exactly one preferred concept")
    concept = preferred[0]
    for term in concept.findall("TermList/Term"):
        if term.get("IsPermutedTermYN") == "Y":
            continue
        alias = (term.findtext("String") or "").strip()
        if alias and alias != label:
            sink.add(
                "synonyms",
                vocabulary_id=vocabulary,
                concept_code=code,
                synonym=alias,
                synonym_type=term.get("LexicalTag", "MeSH term"),
                source="MeSH",
            )
    # MeSH RegistryNumber is polymorphic (CAS, UNII, EC, taxonomy ID or
    # zero). RelatedRegistryNumber can identify another salt or stereoisomer.
    registry = (concept.findtext("RegistryNumber") or "").strip()
    if re.fullmatch(r"[A-Z0-9]{10}", registry):
        sink.add(
            "xrefs",
            vocabulary_id=vocabulary,
            concept_code=code,
            key_type="UNII",
            key_value=registry,
            assertion_source="MeSH",
        )


def read_mesh(config: Mapping[str, Any], sink: Any) -> None:
    """Stream descriptor/SCR record names and exact aliases into contract tables."""
    vocabulary = config["vocabulary_id"]
    prefixes = config.get("descriptor_tree_prefixes")
    if prefixes is not None and (
        not isinstance(prefixes, list)
        or not prefixes
        or not all(
            isinstance(p, str) and re.fullmatch(r"[A-Z](?:[0-9]{2}(?:\.[0-9]{3})*)?", p)
            for p in prefixes
        )
    ):
        raise ValueError("MeSH descriptor_tree_prefixes must list tree prefixes such as D or D03")
    classes = config.get("scr_classes")
    if classes is not None and (
        not isinstance(classes, list)
        or not classes
        or not all(str(c) in {"1", "2", "3", "4", "5", "6"} for c in classes)
    ):
        raise ValueError(
            "MeSH scr_classes must list NLM supplementary record classes (1 through 6)"
        )
    trees = tuple(prefixes or ())
    scr_classes = {str(c) for c in classes or ()}
    files = [
        (key, tag)
        for key, tag in (
            ("descriptors", "DescriptorRecord"),
            ("supplemental", "SupplementalRecord"),
        )
        if config.get(key)
    ]
    if not files:
        raise ValueError("MeSH needs a descriptors or supplemental XML path")
    for key, tag in files:
        path = Path(sink.path(config[key]))
        opener = gzip.open if path.suffix.lower() == ".gz" else open
        with opener(path, "rb") as handle:
            events = ET.iterparse(handle, events=("start", "end"))
            _, root = next(events)
            if root.tag != tag + "Set":
                raise ValueError(f"{path}: expected <{tag}Set>, found <{root.tag}>")
            count = 0
            for event, element in events:
                if event == "end" and element.tag == tag:
                    if tag == "DescriptorRecord":
                        include = not trees or any(
                            (tree.text or "").startswith(trees)
                            for tree in element.iterfind("TreeNumberList/TreeNumber")
                        )
                    else:
                        include = not scr_classes or element.get("SCRClass") in scr_classes
                    if include:
                        _record(element, vocabulary, path, sink)
                        count += 1
                    root.remove(element)
            if count == 0:
                raise ValueError(f"{path}: no {tag} records match the configured filters")
