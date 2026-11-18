"""Convert unpacked OMOP Athena vocabulary files into the client's lexicon tables."""

from __future__ import annotations

import csv
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import duckdb
import yaml

from .schema import DUCKDB_CONFIG

_RXNORM_CLASSES = {
    "Ingredient": "IN",
    "Precise Ingredient": "PIN",
    "Multiple Ingredients": "MIN",
    "Brand Name": "BN",
    "Dose Form": "DF",
    "Dose Form Group": "DFG",
    "Clinical Drug": "SCD",
    "Branded Drug": "SBD",
    "Clinical Drug Comp": "SCDC",
    "Branded Drug Comp": "SBDC",
    "Clinical Drug Form": "SCDF",
    "Branded Drug Form": "SBDF",
    "Clinical Dose Group": "SCDG",
    "Branded Dose Group": "SBDG",
    "Clinical Pack": "GPCK",
    "Branded Pack": "BPCK",
}
_PREDICATES = {
    "RxNorm has ing": "has_ingredient",
    "Has precise ing": "has_precise_ingredient",
    "Has tradename": "has_tradename",
    "RxNorm has dose form": "has_dose_form",
    "Consists of": "consists_of",
}

_INPUT_COLUMNS = {
    "VOCABULARY": ("vocabulary_id", "vocabulary_name", "vocabulary_version"),
    "CONCEPT": (
        "concept_id",
        "concept_name",
        "vocabulary_id",
        "concept_class_id",
        "standard_concept",
        "concept_code",
        "valid_start_date",
        "valid_end_date",
        "invalid_reason",
    ),
    "CONCEPT_SYNONYM": ("concept_id", "concept_synonym_name"),
    "CONCEPT_RELATIONSHIP": ("concept_id_1", "concept_id_2", "relationship_id", "invalid_reason"),
    "CONCEPT_ANCESTOR": ("ancestor_concept_id", "descendant_concept_id"),
}


def _read_inputs(db: duckdb.DuckDBPyConnection, source: Path, *, need_domain: bool) -> None:
    for table, required in _INPUT_COLUMNS.items():
        path = source / f"{table}.csv"
        if not path.exists() and table not in ("VOCABULARY", "CONCEPT"):
            schema = ", ".join(f"{field} VARCHAR" for field in required)
            db.execute(f"CREATE TEMP TABLE {table} ({schema})")
            continue
        if table == "CONCEPT" and need_domain:
            required = (*required, "domain_id")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            header = next(csv.reader(stream, delimiter="\t", quoting=csv.QUOTE_NONE), [])
        missing = set(required) - {column.lower() for column in header}
        if missing:
            raise ValueError(f"{path}: missing Athena column(s) {sorted(missing)}")
        # Supplying the complete schema preserves leading zeroes and bypasses
        # DuckDB's dialect inference for short, header-only and ragged files.
        db.read_csv(
            str(path),
            columns=dict.fromkeys(header, "VARCHAR"),
            header=True,
            delimiter="\t",
            quotechar="",
            escapechar="",
            auto_detect=False,
        ).create_view(table)


def _string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def import_athena(config: Mapping[str, Any], source: Path, work: Path) -> Path:
    """Select and transform an Athena release; return its Parquet table directory."""
    vocabularies = config.get("vocabularies")
    domains = config.get("domains", {})
    if not _string_list(vocabularies):
        raise ValueError("athena.vocabularies must be a nonempty list of vocabulary IDs")
    if not isinstance(domains, dict) or domains.keys() - set(vocabularies):
        raise ValueError("athena.domains must map selected vocabulary IDs to allowed domains")
    if any(not _string_list(allowed) for allowed in domains.values()):
        raise ValueError("each athena.domains selection must be a nonempty list of domain IDs")
    vocabularies = sorted(set(vocabularies))
    work.mkdir(parents=True, exist_ok=True)
    output = work / "athena_tables"
    with TemporaryDirectory(dir=work) as temporary:
        scratch = Path(temporary)
        staged = scratch / "tables"
        staged.mkdir()
        with duckdb.connect(str(scratch / "working.duckdb"), config=DUCKDB_CONFIG) as db:
            _read_inputs(db, source, need_domain=bool(domains))
            versions = db.execute(
                """
                SELECT vocabulary_id, vocabulary_name, vocabulary_version FROM vocabulary
                WHERE vocabulary_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY vocabulary_id
            """,
                [vocabularies],
            ).fetchall()
            missing = set(vocabularies) - {v[0] for v in versions}
            if missing:
                raise ValueError(
                    f"{source}/VOCABULARY.csv: missing vocabulary IDs {sorted(missing)}"
                )
            _select_concepts(db, vocabularies, domains)
            _write_tables(db, staged)
        declaration = {
            "vocabularies": [
                {
                    "vocabulary_id": vocab,
                    "release": version or "unversioned",
                    "license": config.get("license", "unspecified"),
                    "description": name,
                }
                for vocab, name, version in versions
            ]
        }
        (staged / "vocabularies.yaml").write_text(
            yaml.safe_dump(declaration, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        staged.rename(output)
    return output


def _select_concepts(
    db: duckdb.DuckDBPyConnection, vocabularies: list[str], domains: dict[str, list[str]]
) -> None:
    db.execute("CREATE TEMP TABLE selection(vocab VARCHAR, domain VARCHAR)")
    db.executemany(
        "INSERT INTO selection VALUES (?, ?)",
        [(vocab, domain) for vocab in vocabularies for domain in set(domains.get(vocab, [None]))],
    )
    db.execute("CREATE TEMP TABLE tty(class_name VARCHAR, code VARCHAR)")
    db.executemany("INSERT INTO tty VALUES (?, ?)", list(_RXNORM_CLASSES.items()))
    domain_filter = "AND (f.domain IS NULL OR f.domain = c.domain_id)" if domains else ""
    db.execute(f"""
        CREATE TABLE selected AS
        SELECT c.*, coalesce(t.code, c.concept_class_id) AS term_type
        FROM concept c JOIN selection f ON f.vocab = c.vocabulary_id {domain_filter}
        LEFT JOIN tty t ON c.concept_class_id = t.class_name
            AND c.vocabulary_id IN ('RxNorm', 'RxNorm Extension')
    """)
    represented = {
        v for (v,) in db.execute("SELECT DISTINCT vocabulary_id FROM selected").fetchall()
    }
    missing = set(vocabularies) - represented
    if missing:
        raise ValueError(f"no Athena concepts match the configured selection for {sorted(missing)}")


def _write_tables(db: duckdb.DuckDBPyConnection, directory: Path) -> None:
    db.execute("CREATE TEMP TABLE predicates(relationship_id VARCHAR, predicate VARCHAR)")
    db.executemany("INSERT INTO predicates VALUES (?, ?)", list(_PREDICATES.items()))
    # CONCEPT_RELATIONSHIP can be large. Retain only current outgoing links
    # from this vocabulary/domain subset and read that release file once.
    db.execute("""
        CREATE TABLE edges AS
        SELECT r.concept_id_1, r.concept_id_2, r.relationship_id
        FROM concept_relationship r SEMI JOIN selected s ON s.concept_id = r.concept_id_1
        WHERE coalesce(trim(r.invalid_reason), '') = '' AND r.concept_id_1 <> r.concept_id_2
            AND (r.relationship_id IN (SELECT relationship_id FROM predicates)
                 OR r.relationship_id IN ('Maps to', 'Concept replaced by'))
    """)
    db.execute("""
        CREATE TEMP VIEW concepts AS
        WITH replacement AS (
            SELECT r.concept_id_1, min(b.concept_code) AS replaced_by
            FROM edges r JOIN selected a ON a.concept_id = r.concept_id_1
            LEFT JOIN selected b ON b.concept_id = r.concept_id_2 AND b.vocabulary_id = a.vocabulary_id
            WHERE r.relationship_id = 'Concept replaced by'
            GROUP BY r.concept_id_1 HAVING count(DISTINCT r.concept_id_2) = 1
        )
        SELECT vocabulary_id, concept_code, concept_name, term_type AS concept_class,
            CASE coalesce(upper(trim(invalid_reason)), '')
                WHEN '' THEN 'active' WHEN 'D' THEN 'obsolete' WHEN 'U' THEN 'remapped'
                ELSE error('unrecognised Athena invalid_reason: ' || invalid_reason) END AS status,
            strftime(strptime(nullif(trim(valid_start_date),''), '%Y%m%d'), '%Y-%m-%d') AS valid_start_date,
            strftime(strptime(nullif(trim(valid_end_date),''), '%Y%m%d'), '%Y-%m-%d') AS valid_end_date,
            r.replaced_by
        FROM selected s LEFT JOIN replacement r ON s.concept_id = r.concept_id_1
    """)
    db.execute("""
        CREATE TEMP VIEW synonyms AS
        SELECT DISTINCT s.vocabulary_id, s.concept_code, y.concept_synonym_name AS synonym,
            'synonym' AS synonym_type, s.vocabulary_id AS source
        FROM concept_synonym y JOIN selected s USING(concept_id)
        WHERE trim(y.concept_synonym_name) <> ''
    """)
    db.execute("""
        CREATE TEMP VIEW relationships AS
        SELECT a.vocabulary_id AS vocabulary_id_1, a.concept_code AS concept_code_1, p.predicate,
            b.vocabulary_id AS vocabulary_id_2, b.concept_code AS concept_code_2
        FROM edges r JOIN predicates p USING(relationship_id)
        JOIN selected a ON r.concept_id_1 = a.concept_id
        JOIN selected b ON r.concept_id_2 = b.concept_id
        UNION
        SELECT d.vocabulary_id, d.concept_code, 'has_ingredient', a.vocabulary_id, a.concept_code
        FROM concept_ancestor x JOIN selected a ON a.concept_id = x.ancestor_concept_id
        JOIN selected d ON d.concept_id = x.descendant_concept_id
        WHERE a.concept_id <> d.concept_id AND a.concept_class_id = 'Ingredient'
          AND a.vocabulary_id IN ('RxNorm', 'RxNorm Extension')
          AND d.vocabulary_id IN ('RxNorm', 'RxNorm Extension')
    """)
    db.execute("""
        CREATE TEMP VIEW xrefs AS
        SELECT vocabulary_id, concept_code, 'OMOP_CONCEPT_ID' AS key_type, concept_id AS key_value,
            'OMOP' AS assertion_source FROM selected
        UNION
        SELECT vocabulary_id, concept_code, 'OMOP_STANDARD', concept_id, 'OMOP'
        FROM selected WHERE standard_concept = 'S'
        UNION
        SELECT a.vocabulary_id, a.concept_code, 'OMOP_STANDARD', r.concept_id_2, 'OMOP'
        FROM edges r JOIN selected a ON a.concept_id = r.concept_id_1
        WHERE r.relationship_id = 'Maps to'
    """)
    for table in ("concepts", "synonyms", "relationships", "xrefs"):
        db.sql(f"SELECT * FROM {table} ORDER BY ALL").write_parquet(
            str(directory / f"{table}.parquet")
        )
