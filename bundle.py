"""Build a portable DuckDB dictionary and its exact-name index from prepared CSVs."""

import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from .schema import DUCKDB_CONFIG, OUTPUTS, read_table_csv


def normalize_name(value: str) -> str:
    """Build the exact lookup key while retaining punctuation, qualifiers and token order."""
    return " ".join(value.split()).casefold()


def build_bundle(tables: Path, output: Path, declaration: dict) -> None:
    """Publish the database and its manifest together, without overwriting an existing build."""
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"{output} already contains data")
    output.parent.mkdir(parents=True, exist_ok=True)
    aliases = declaration.get("source_aliases", {})
    counts = {}
    with TemporaryDirectory(prefix=".mprint-bundle-", dir=output.parent) as tmp:
        staging = Path(tmp) / "bundle"
        staging.mkdir()
        database = staging / "lexicon.duckdb"
        with duckdb.connect(str(database), config=DUCKDB_CONFIG) as con:
            for table, spec in OUTPUTS.items():
                path = tables / f"{table}.csv"
                if table == "product_evidence" and not path.is_file():
                    continue
                read_table_csv(con, path, spec).create(table)
                counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchall()[0][0]

            con.execute("CREATE TEMP TABLE aliases(raw VARCHAR, canonical VARCHAR)")
            if aliases:
                con.executemany("INSERT INTO aliases VALUES (?, ?)", list(aliases.items()))
            con.execute("""
                CREATE TEMP VIEW dictionary_names AS
                SELECT vocabulary_id, concept_code, concept_name AS matched_name,
                       'preferred' AS name_type, name_source AS source FROM concepts
                UNION ALL
                SELECT vocabulary_id, concept_code, synonym AS matched_name,
                       coalesce(nullif(synonym_type, ''), 'synonym'), source FROM synonyms
            """)
            # Compute each distinct Unicode key once in Python without a resident full-name map.
            keys = Path(tmp) / "normalized.csv"
            cursor = con.execute("SELECT DISTINCT matched_name FROM dictionary_names")
            with keys.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("matched_name", "normalized_name"))
                while batch := cursor.fetchmany(8192):
                    writer.writerows((name, normalize_name(name)) for (name,) in batch)
            con.execute(
                """
                CREATE TABLE exact_names AS
                WITH normalized AS (
                    SELECT DISTINCT k.normalized_name,
                           n.vocabulary_id, n.concept_code, n.matched_name, n.name_type,
                           coalesce(a.canonical, nullif(n.source, ''), n.vocabulary_id) AS source
                    FROM dictionary_names n
                    JOIN read_csv(?, header=true, all_varchar=true, delim=',', quote='"', escape='"') k
                      USING (matched_name)
                    LEFT JOIN aliases a ON a.raw = coalesce(nullif(n.source, ''), n.vocabulary_id)
                )
                SELECT * FROM normalized WHERE normalized_name <> ''
                ORDER BY normalized_name, vocabulary_id, concept_code, matched_name, name_type, source
            """,
                [str(keys)],
            )
            con.execute("CREATE INDEX exact_name_lookup ON exact_names(normalized_name)")
            counts["exact_names"] = con.execute("SELECT count(*) FROM exact_names").fetchall()[0][0]

        with database.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {
            "format": "mprint-exact-names",
            "format_version": 1,
            "database": database.name,
            "sha256": digest,
            "counts": counts,
            "normalization": "Python Unicode whitespace split/join followed by casefold",
            "vocabularies": declaration["vocabularies"],
            "source_aliases": aliases,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if output.exists():
            output.rmdir()  # Windows cannot rename onto an existing directory.
        staging.rename(output)
