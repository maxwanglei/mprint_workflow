"""Regression checks for the MPRINT preparation command using invented local records."""

import csv
import hashlib
import json
from pathlib import Path

import duckdb
import pytest
import yaml

from mprint_workflow.bundle import normalize_name
from mprint_workflow.prepare_dictionaries import prepare

DEMO = Path(__file__).parent / "demo" / "sources.yaml"


def test_qualified_codes_and_asserting_source_survive_exact_lookup(tmp_path):
    output = tmp_path / "demo"
    counts = prepare(DEMO, output, [], True)
    assert counts["concepts"] == 3
    bundle = output / "bundle"
    manifest = json.loads((bundle / "manifest.json").read_text())
    database = bundle / manifest["database"]
    assert manifest["format"] == "mprint-exact-names"
    assert manifest["format_version"] == 1
    assert manifest["counts"]["concepts"] == 3
    assert manifest["source_aliases"]["NCI"] == "NCIt"
    with database.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == manifest["sha256"]
    with duckdb.connect(str(database), read_only=True) as db:
        sql = "SELECT vocabulary_id, concept_code, source FROM exact_names WHERE normalized_name=?"
        assert set(db.execute(sql, [normalize_name("EXAMPLE drug")]).fetchall()) == {
            ("UMLS", "C9900001", "NCIt"),
            ("NCIt", "C99900001", "NCIt"),
        }
        assert db.execute(sql, [normalize_name("Example drug 50 mg")]).fetchall() == []


def test_unicode_names_preserve_original_form_and_deduplicate_aliases(tmp_path):
    prepared = tmp_path / "prepared"
    prepare(DEMO, prepared, [], False)
    alias = "Straße\u2003Tablette"
    with (prepared / "synonyms.csv").open("a", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerows([("NCIt", "C99900001", alias, "synonym", "NCI")] * 2)
    config = tmp_path / "sources.yaml"
    config.write_text(
        yaml.safe_dump({"sources": {"previous": {"format": "tables", "path": "prepared"}}})
    )
    output = tmp_path / "indexed"
    prepare(config, output, [], True)
    with duckdb.connect(str(output / "bundle" / "lexicon.duckdb"), read_only=True) as db:
        result = db.execute(
            "SELECT matched_name, name_type, source FROM exact_names WHERE normalized_name=?",
            [normalize_name("  STRASSE  Tablette  ")],
        ).fetchall()
        assert result == [(alias, "synonym", "NCIt")]


def test_subset_and_failed_merge_preserve_existing_output(tmp_path):
    output = tmp_path / "partial"
    prepare(DEMO, output, ["ncit"], False)
    with (output / "concepts.csv").open() as file:
        assert {row["vocabulary_id"] for row in csv.DictReader(file)} == {"NCIt"}
    with pytest.raises(ValueError, match="already contains data"):
        prepare(DEMO, output, ["umls"], False)
    # Exercise reimport of prepared tables plus duplicate-key detection.
    config = tmp_path / "merged.yaml"
    config.write_text(
        yaml.safe_dump({"sources": {"subset": {"format": "tables", "path": "partial"}}})
    )
    merged = tmp_path / "merged"
    with (output / "concepts.csv").open() as file:
        first = file.readlines()[1]
    with (output / "concepts.csv").open("a") as file:
        file.write(first)
    with pytest.raises(ValueError, match="duplicate concept key"):
        prepare(config, merged, [], False)
    assert not merged.exists()


def test_shared_fda_name_keeps_both_product_keys_and_strengths(tmp_path):
    (tmp_path / "products.txt").write_text(
        "Appl_Type~Appl_No~Product_No~Trade_Name~Ingredient~Strength~DF;Route~Applicant\n"
        "N~000123~001~Example product~Example drug~5MG~TABLET;ORAL~Example firm\n"
        "N~000123~002~Example product~Example drug~10MG~TABLET;ORAL~Example firm\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "fda": {
                        "format": "orange_book",
                        "path": "products.txt",
                        "vocabulary_id": "OrangeBook",
                        "release": "demo",
                    }
                }
            }
        )
    )
    output = tmp_path / "regulatory"
    prepare(config, output, [], True)
    with duckdb.connect(str(output / "bundle" / "lexicon.duckdb"), read_only=True) as db:
        sql = "SELECT concept_code FROM exact_names WHERE normalized_name=? ORDER BY concept_code"
        assert db.execute(sql, ["example product"]).fetchall() == [("000123-001",), ("000123-002",)]
        assert db.execute(sql, ["example drug"]).fetchall() == []
        assert db.execute(
            "SELECT DISTINCT application_number FROM product_evidence"
        ).fetchall() == [("000123",)]
    with (output / "product_evidence.csv").open() as file:
        data = list(csv.DictReader(file))
    assert [(r["application_number"], r["ingredient_text"], r["strength"]) for r in data] == [
        ("000123", "Example drug", "5MG"),
        ("000123", "Example drug", "10MG"),
    ]
