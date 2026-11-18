"""Prepare MPRINT dictionary tables from local releases configured in YAML."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import zlib
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import duckdb
import yaml

# Also allow "python mprint_workflow/prepare_dictionaries.py" from a source checkout.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "mprint_workflow"

from .athena import import_athena
from .bundle import build_bundle
from .mesh import read_mesh
from .ncit import read_ncit
from .products import read_article57, read_drugsfda, read_orange_book
from .schema import (
    CONCEPT_STATUSES,
    DUCKDB_CONFIG,
    OUTPUTS,
    TABLES,
    _text,
    load_declaration,
    read_table_csv,
    validate_aliases,
    validate_declaration,
)
from .substances import read_drugbank, read_gsrs
from .umls import read_umls

READERS = {
    "article57": read_article57,
    "drugbank": read_drugbank,
    "drugsfda": read_drugsfda,
    "gsrs": read_gsrs,
    "mesh": read_mesh,
    "ncit": read_ncit,
    "orange_book": read_orange_book,
    "umls": read_umls,
}


OPTIONS = {
    "athena": {"path", "vocabularies", "domains", "license"},
    "tables": {"path"},
    "umls": {"mrconso", "sources", "language", "ttys", "mrsty", "semantic_types"},
    "mesh": {"descriptors", "supplemental", "descriptor_tree_prefixes", "scr_classes"},
    "ncit": {"path", "semantic_types"},
    "drugbank": {"path"},
    "gsrs": {"path"},
    "drugsfda": {"path", "encoding"},
    "orange_book": {"path", "encoding"},
    "article57": {"path", "encoding", "sheet", "code_column"},
}
REQUIRED = {
    "athena": frozenset({"path"}),
    "tables": frozenset({"path"}),
    "umls": frozenset({"mrconso"}),
    "mesh": frozenset(),
    **dict.fromkeys(
        ("ncit", "drugbank", "gsrs", "drugsfda", "orange_book", "article57"), frozenset({"path"})
    ),
}
NATIVE_OPTIONS = frozenset({"vocabulary_id", "license", "release"})
NATIVE_REQUIRED = frozenset({"vocabulary_id", "release"})
TEXT_OPTIONS = frozenset(
    {
        "path",
        "mrconso",
        "mrsty",
        "descriptors",
        "supplemental",
        "encoding",
        "sheet",
        "code_column",
        "vocabulary_id",
        "license",
    }
)
INPUT_ERRORS = (
    ValueError,
    OSError,
    csv.Error,
    ParseError,
    BadZipFile,
    duckdb.Error,
    EOFError,
    zlib.error,
    sqlite3.Error,
)


class TableSink:
    """Stream normalized dictionary tables to disk and create evidence output on first use."""

    def __init__(self, source_root, destination, stack):
        self.source_root = source_root
        self.counts = Counter()
        self.writers = {}
        self.stack = stack
        self.destination = destination
        for spec in TABLES.values():
            self.open_table(spec)

    def open_table(self, spec):
        file = self.stack.enter_context(
            (self.destination / f"{spec.name}.csv").open("w", encoding="utf-8", newline="")
        )
        writer = csv.DictWriter(file, fieldnames=spec.columns)
        writer.writeheader()
        self.writers[spec.name] = writer

    def path(self, name):
        return self.source_root / Path(name).expanduser()

    def add(self, table, **row):
        spec = OUTPUTS[table]
        row = {k: v.strip() if isinstance(v, str) else v for k, v in row.items()}
        if table not in self.writers:
            self.open_table(spec)
        for column in spec.required:
            if row.get(column) is None or not str(row[column]).strip():
                raise ValueError(f"{table}: missing {column}")
        self.writers[table].writerow(row)
        self.counts[table] += 1


def _parquet_rows(result, columns):
    while batch := result.fetchmany(8192):
        for values in batch:
            yield dict(zip(columns, values, strict=True))


def _csv_rows(reader, path):
    for row in reader:
        if None in row:
            raise ValueError(f"{path}:{reader.line_num}: more fields than the header")
        yield row


def copy_tables(directory, sink):
    """Append a prepared dictionary directory, including optional per-product evidence."""
    for name, spec in OUTPUTS.items():
        files = [directory / f"{name}.{ext}" for ext in ("csv", "tsv", "parquet")]
        found = [f for f in files if f.is_file()]
        if len(found) > 1 or (not found and spec.mandatory):
            raise ValueError(f"{directory}: expected one {name} table, found {len(found)}")
        if not found:
            continue
        path = found[0]
        with ExitStack() as stack:
            if path.suffix == ".parquet":
                db = stack.enter_context(duckdb.connect(config=DUCKDB_CONFIG))
                result = db.read_parquet(str(path)).execute()
                columns = result.columns
                untyped = [c for c, t in zip(columns, result.types, strict=True) if t != "VARCHAR"]
                if untyped:
                    raise ValueError(
                        f"{path}: non-text column(s) {untyped}; store values as strings"
                    )
                rows = _parquet_rows(result, columns)
            else:
                file = stack.enter_context(path.open(encoding="utf-8-sig", newline=""))
                reader = csv.DictReader(file, delimiter="\t" if path.suffix == ".tsv" else ",")
                columns = reader.fieldnames or []
                rows = _csv_rows(reader, path)
            if len(set(columns)) != len(columns):
                raise ValueError(f"{path}: duplicate column in header {columns}")
            if set(spec.required) - set(columns) or set(columns) - set(spec.columns):
                raise ValueError(f"{path}: columns do not match the {name} contract: {columns}")
            for row in rows:
                sink.add(name, **row)


def merge_aliases(existing, incoming):
    for original, name in incoming.items():
        if original in existing and existing[original] != name:
            raise ValueError(f"conflicting source aliases for {original!r}")
        existing[original] = name


def publish_tables(raw, staging, declaration, scratch):
    """Validate the raw tables, then write each one sorted and without identical rows."""
    with duckdb.connect(str(scratch / "checks.duckdb"), config=DUCKDB_CONFIG) as db:
        present = [name for name in OUTPUTS if (raw / f"{name}.csv").exists()]
        for name in present:
            read_table_csv(db, raw / f"{name}.csv", OUTPUTS[name]).create_view(name)
        invalid = db.execute(
            "SELECT vocabulary_id, concept_code FROM concepts "
            "WHERE (status IS NOT NULL AND status NOT IN (SELECT unnest(?))) "
            "OR (valid_start_date IS NOT NULL AND try_strptime(valid_start_date, '%Y-%m-%d') IS NULL) "
            "OR (valid_end_date IS NOT NULL AND try_strptime(valid_end_date, '%Y-%m-%d') IS NULL) LIMIT 1",
            [list(CONCEPT_STATUSES)],
        ).fetchone()
        if invalid:
            raise ValueError(f"invalid status or date for concept {invalid}")
        db.execute("CREATE TABLE ids AS SELECT vocabulary_id, concept_code FROM concepts")
        duplicates = db.execute(
            "SELECT vocabulary_id, concept_code FROM ids GROUP BY ALL HAVING count(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicates:
            raise ValueError(
                f"duplicate concept key {duplicates}; supply one authoritative row per vocabulary/code"
            )
        declared = {v["vocabulary_id"] for v in declaration["vocabularies"]}
        actual = {v for (v,) in db.execute("SELECT DISTINCT vocabulary_id FROM ids").fetchall()}
        if actual != declared:
            raise ValueError(
                f"vocabulary mismatch: missing concepts {sorted(declared - actual)}, "
                f"undeclared vocabularies {sorted(actual - declared)}"
            )
        dangling = db.execute(
            "SELECT c.vocabulary_id, c.concept_code, c.replaced_by FROM concepts c "
            "WHERE c.replaced_by IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ids i WHERE "
            "i.vocabulary_id = c.vocabulary_id AND i.concept_code = c.replaced_by) LIMIT 1"
        ).fetchone()
        if dangling:
            raise ValueError(f"replacement refers to a missing concept: {dangling}")
        references = [
            ("synonyms", ""),
            ("xrefs", ""),
            ("relationships", "_1"),
            ("relationships", "_2"),
        ]
        if "product_evidence" in present:
            references.append(("product_evidence", ""))
        for table, suffix in references:
            missing = db.execute(
                f"SELECT t.vocabulary_id{suffix}, t.concept_code{suffix} FROM {table} t "
                f"ANTI JOIN ids i ON i.vocabulary_id = t.vocabulary_id{suffix} "
                f"AND i.concept_code = t.concept_code{suffix} LIMIT 1"
            ).fetchone()
            if missing:
                raise ValueError(f"{table}: missing concept {missing}")
        counts = Counter()
        for name in present:
            distinct = "" if name == "concepts" else "DISTINCT "
            target = str(staging / f"{name}.csv").replace("'", "''")
            (counts[name],) = db.execute(
                f"COPY (SELECT {distinct}* FROM {name} ORDER BY ALL) TO '{target}' "
                "(HEADER, DELIMITER ',', QUOTE '\"', ESCAPE '\"')"
            ).fetchone()
    return counts


def validate_source(settings):
    """Check one source mapping's shape and option types; return its format."""
    if not isinstance(settings, dict):
        raise ValueError("expected a source mapping")
    if "format" not in settings:
        raise ValueError("missing required option 'format'")
    kind = settings["format"]
    if not isinstance(kind, str) or kind not in OPTIONS:
        raise ValueError(f"unknown format {kind!r}; choose from {sorted(OPTIONS)}")
    native = kind in READERS
    allowed = OPTIONS[kind] | {"format"} | (NATIVE_OPTIONS if native else frozenset())
    required = REQUIRED[kind] | (NATIVE_REQUIRED if native else frozenset())
    unknown = settings.keys() - allowed
    if unknown:
        raise ValueError(f"unknown options for {kind}: {sorted(unknown)}")
    missing = sorted(required - settings.keys())
    if missing:
        raise ValueError(f"missing required option {missing[0]!r}")
    for option in sorted(TEXT_OPTIONS & settings.keys()):
        _text(settings[option], option)
    return kind


def prepare(config_path, output, only, compile_bundle):
    csv.field_size_limit(2**31 - 1)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if (
        not isinstance(config, dict)
        or not isinstance(config.get("sources"), dict)
        or not config["sources"]
    ):
        raise ValueError("config must contain a nonempty 'sources' mapping")
    sources = config["sources"]
    unknown = set(only) - sources.keys()
    if unknown:
        raise ValueError(f"unknown source name(s): {sorted(unknown)}; choices: {list(sources)}")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"output {output} already contains data; choose a fresh directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    vocabularies = []
    aliases = validate_aliases(config.get("source_aliases", {}))
    with TemporaryDirectory(prefix=".mprint-", dir=output.parent) as tmp:
        scratch = Path(tmp)
        staging = scratch / "output"
        staging.mkdir()
        raw = scratch / "raw"
        raw.mkdir()
        with ExitStack() as stack:
            sink = TableSink(config_path.parent, raw, stack)
            for name, settings in sources.items():
                if only and name not in only:
                    continue
                try:
                    kind = validate_source(settings)
                    before = sink.counts["concepts"]
                    if kind in ("athena", "tables"):
                        directory = sink.path(settings["path"])
                        if kind == "athena":
                            directory = import_athena(
                                settings, directory, scratch / f"athena-{len(vocabularies)}"
                            )
                        declaration = load_declaration(directory / "vocabularies.yaml")
                        vocabularies.extend(declaration["vocabularies"])
                        merge_aliases(aliases, declaration["source_aliases"])
                        validate_declaration(
                            {"vocabularies": vocabularies, "source_aliases": aliases}
                        )
                        copy_tables(directory, sink)
                    else:
                        release = settings["release"]
                        if not release or str(release).strip() == "REPLACE_WITH_RELEASE":
                            raise ValueError("set release to the actual release or snapshot date")
                        vocabularies.append(
                            {
                                "vocabulary_id": settings["vocabulary_id"],
                                "release": str(release),
                                "license": settings.get("license", "unspecified"),
                            }
                        )
                        validate_declaration(
                            {"vocabularies": vocabularies, "source_aliases": aliases}
                        )
                        READERS[kind](settings, sink)
                    added = sink.counts["concepts"] - before
                    if not added:
                        raise ValueError("no concepts matched the input and filters")
                    print(f"{name}: {added:,} concepts", file=sys.stderr)
                except INPUT_ERRORS as exc:
                    raise ValueError(f"source {name!r}: {exc}") from exc
        declaration = validate_declaration(
            {"vocabularies": vocabularies, "source_aliases": aliases}
        )
        counts = publish_tables(raw, staging, declaration, scratch)
        (staging / "vocabularies.yaml").write_text(
            yaml.safe_dump(declaration, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        if compile_bundle:
            build_bundle(staging, staging / "bundle", declaration)
        staging.rename(output)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", required=True, type=Path, help="YAML describing local dictionary releases"
    )
    parser.add_argument("--output", required=True, type=Path, help="New or empty output directory")
    parser.add_argument(
        "--only", nargs="+", default=[], metavar="SOURCE", help="Import only these named sources"
    )
    parser.add_argument(
        "--build", action="store_true", help="Also compile and validate the DuckDB bundle"
    )
    args = parser.parse_args()
    try:
        counts = prepare(args.config.resolve(), args.output.resolve(), args.only, args.build)
    except (*INPUT_ERRORS, yaml.YAMLError) as exc:
        parser.exit(1, f"error: {exc}\n")
    for table in OUTPUTS:
        if table == "product_evidence" and not counts[table]:
            continue
        print(f"{table}.csv: {counts[table]:,} rows")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
