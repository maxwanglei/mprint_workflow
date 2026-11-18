"""Published tables are validated, trimmed and free of identical rows, whatever the reader."""

import csv
import io
from pathlib import Path

import duckdb
import pytest
import yaml

from mprint_workflow.bundle import build_bundle
from mprint_workflow.prepare_dictionaries import prepare

NATIVE = {"vocabulary_id": "V", "release": "r"}
TABLES = {"format": "tables", "path": "t"}
DECLARATION = "vocabularies: [{vocabulary_id: V, release: r}]\n"
CONCEPT = "vocabulary_id,concept_code,concept_name\nV,1,a\n"


def test_identical_umls_atoms_write_one_synonym(run_source, read_rows):
    rows = [
        "C1|ENG|P|L|PF|S1|Y|A1||||NCI|PT|C1|Drug A||N||",
        "C1|ENG|S|L|VO|S2|N|A2||||NCI|SY|C1|Alias||N||",
        "C1|ENG|S|L|VO|S2|N|A3||||NCI|SY|C2|Alias||N||",
    ]
    out = run_source(
        {"format": "umls", **NATIVE, "mrconso": "m.rrf", "sources": ["NCI"]},
        {"m.rrf": "\n".join(rows) + "\n"},
    )
    assert [r["synonym"] for r in read_rows(out, "synonyms")].count("Alias") == 1


def test_identical_evidence_rows_write_once(run_source, read_rows):
    products = (
        "Appl_Type~Appl_No~Product_No~Trade_Name~TE_Code\n"
        "N~000123~001~Example~AB1\nN~000123~001~Example~AB2\n"
    )
    out = run_source({"format": "orange_book", **NATIVE, "path": "p.txt"}, {"p.txt": products})
    assert len(read_rows(out, "product_evidence")) == 1


def test_text_fields_are_trimmed(run_source, read_rows):
    products = (
        "Appl_No~Product_No~Trade_Name~Strength~Applicant_Full_Name\n"
        "000123~001~Example~5MG ~ Example firm\n"
    )
    out = run_source({"format": "orange_book", **NATIVE, "path": "p.txt"}, {"p.txt": products})
    [row] = read_rows(out, "product_evidence")
    assert (row["strength"], row["holder"]) == ("5MG", "Example firm")


def test_uppercase_status_rejected(run_source):
    files = {
        "t/vocabularies.yaml": DECLARATION,
        "t/concepts.csv": "vocabulary_id,concept_code,concept_name,status\nV,1,a,ACTIVE\n",
    }
    with pytest.raises(ValueError, match="invalid status or date"):
        run_source(TABLES, files)


def test_quoted_name_after_sniff_window_validates(run_source):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["vocabulary_id", "concept_code", "synonym", "synonym_type", "source"])
    writer.writerows(["V", "1", f"alias {i}", "SY", "NCI"] for i in range(30_000))
    writer.writerow(["V", "1", 'Example "quoted", salt', "SY", "NCI"])
    files = {
        "t/vocabularies.yaml": DECLARATION,
        "t/concepts.csv": CONCEPT,
        "t/synonyms.csv": buffer.getvalue(),
    }
    out = run_source(TABLES, files, build=True)
    with duckdb.connect(str(out / "bundle" / "lexicon.duckdb"), read_only=True) as db:
        sql = "SELECT count(*) FROM synonyms WHERE synonym = 'Example \"quoted\", salt'"
        assert db.execute(sql).fetchall() == [(1,)]


@pytest.mark.parametrize(
    "table, header, row",
    [
        ("synonyms", "vocabulary_id,concept_code,synonym", "V,absent,alias"),
        ("xrefs", "vocabulary_id,concept_code,key_type,key_value", "V,absent,UNII,ABCDEFGHIJ"),
        (
            "relationships",
            "vocabulary_id_1,concept_code_1,predicate,vocabulary_id_2,concept_code_2",
            "V,1,has_ingredient,V,absent",
        ),
        ("product_evidence", "vocabulary_id,concept_code,product_name", "V,absent,Name"),
    ],
)
def test_dangling_reference_rejected(run_source, table, header, row):
    files = {
        "t/vocabularies.yaml": DECLARATION,
        "t/concepts.csv": CONCEPT,
        f"t/{table}.csv": f"{header}\n{row}\n",
    }
    with pytest.raises(ValueError, match=f"{table}: missing concept"):
        run_source(TABLES, files)


def test_bad_date_rejected(run_source):
    files = {
        "t/vocabularies.yaml": DECLARATION,
        "t/concepts.csv": "vocabulary_id,concept_code,concept_name,valid_start_date\nV,1,a,2024-13-40\n",
    }
    with pytest.raises(ValueError, match="invalid status or date"):
        run_source(TABLES, files)


def test_missing_replacement_rejected(run_source):
    files = {
        "t/vocabularies.yaml": DECLARATION,
        "t/concepts.csv": "vocabulary_id,concept_code,concept_name,replaced_by\nV,1,a,absent\n",
    }
    with pytest.raises(ValueError, match="replacement refers to a missing concept"):
        run_source(TABLES, files)


def test_vocabularies_yaml_keeps_unicode(run_source):
    out = run_source(
        {"format": "ncit", **NATIVE, "path": "t.txt", "license": "Lizenz für Forschung"},
        {"t.txt": "C1\tiri\t\tDrug A\t\t\t\tPharmacologic Substance\n"},
    )
    assert "für" in (out / "vocabularies.yaml").read_text(encoding="utf-8")


DEMO = Path(__file__).parent / "demo" / "sources.yaml"


@pytest.fixture
def windows_rename(monkeypatch):
    original = Path.rename

    def rename(self, target):
        if Path(target).exists():
            raise FileExistsError(target)
        return original(self, target)

    monkeypatch.setattr(Path, "rename", rename)


def test_existing_empty_output_publishes_on_windows(windows_rename, tmp_path):
    (tmp_path / "out").mkdir()
    prepare(DEMO, tmp_path / "out", [], True)
    assert (tmp_path / "out" / "bundle" / "manifest.json").is_file()


def test_bundle_into_existing_empty_directory_on_windows(windows_rename, tmp_path):
    prepared = tmp_path / "prepared"
    prepare(DEMO, prepared, [], False)
    (tmp_path / "bundle").mkdir()
    declaration = yaml.safe_load((prepared / "vocabularies.yaml").read_text(encoding="utf-8"))
    build_bundle(prepared, tmp_path / "bundle", declaration)
    assert (tmp_path / "bundle" / "manifest.json").is_file()


def test_output_cannot_be_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="current directory"):
        prepare(DEMO, Path.cwd(), [], False)
