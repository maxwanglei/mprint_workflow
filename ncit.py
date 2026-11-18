"""Read the unpacked, tab-delimited NCI Thesaurus FLAT release."""

import csv


def read_ncit(config, sink):
    vocabulary = config["vocabulary_id"]
    path = sink.path(config["path"])
    selected_types = config.get("semantic_types")
    if selected_types is not None and (
        not isinstance(selected_types, list)
        or not selected_types
        or not all(isinstance(t, str) and t.strip() for t in selected_types)
    ):
        raise ValueError("semantic_types must be a nonempty list of NCI semantic type names")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.reader(stream, delimiter="\t", quoting=csv.QUOTE_NONE), 1):
            if len(row) not in (8, 9):
                raise ValueError(f"{path}:{line}: expected 8 or 9 columns in the NCIt flat release")
            code, _, _, names, _, _, status, types = row[:8]
            if selected_types and not set(selected_types).intersection(types.split("|")):
                continue
            terms = list(dict.fromkeys(s.strip() for s in names.split("|") if s.strip()))
            if not terms:
                raise ValueError(f"{path}:{line}: no preferred name for {code}")
            states = {s.casefold().replace("_", " ") for s in status.split("|")}
            obsolete = bool(states & {"retired concept", "obsolete concept", "retired", "obsolete"})
            sink.add(
                "concepts",
                vocabulary_id=vocabulary,
                concept_code=code,
                concept_name=terms[0],
                concept_class=types,
                status="obsolete" if obsolete else "active",
            )
            for synonym in terms[1:]:
                sink.add(
                    "synonyms",
                    vocabulary_id=vocabulary,
                    concept_code=code,
                    synonym=synonym,
                    synonym_type="synonym",
                    source=vocabulary,
                )
