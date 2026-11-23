"""Small synthetic Athena releases exercise the standalone converter."""

import tempfile
import unittest
from pathlib import Path

import duckdb
import yaml

from mprint_workflow.athena import import_athena
from mprint_workflow.prepare_dictionaries import prepare


class AthenaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "input"
        self.source.mkdir()
        self.config = {
            "vocabularies": ["RxNorm", "RxNorm Extension", "SNOMED"],
            "domains": {"SNOMED": ["Drug"]},
            "license": "fixture",
        }
        self.write(
            "VOCABULARY",
            [
                "vocabulary_id\tvocabulary_name\tvocabulary_version",
                "RxNorm\tSynthetic RxNorm\t2026",
                "SNOMED\tSynthetic SNOMED\t999",
                "RxNorm Extension\tSynthetic Extension\t3",
            ],
        )
        self.write(
            "CONCEPT",
            [
                "concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\tstandard_concept\tconcept_code\tvalid_start_date\tvalid_end_date\tinvalid_reason",
                '1\tExample "quoted"\tDrug\tRxNorm\tIngredient\tS\t001\t19700101\t20991231\t',
                "2\tExample pack\tDrug\tRxNorm Extension\tBranded Pack\tS\t005\t20141201\t20991231\t",
                "3\tOld\tDrug\tRxNorm\tIngredient\t\t002\t19700101\t20000130\tU",
                "4\tOlder\tDrug\tRxNorm\tIngredient\t\t003\t19700101\t20000130\tD",
                "5\tExample term\tDrug\tSNOMED\tSubstance\t\t100\t19700101\t20991231\t",
                "6\tFinding\tCondition\tSNOMED\tClinical Finding\tS\t200\t19700101\t20991231\t",
            ],
        )

    def write(self, name, rows):
        (self.source / f"{name}.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    def read(self, output, name):
        with duckdb.connect() as db:
            return db.read_parquet(str(output / f"{name}.parquet")).fetchall()

    def run_import(self):
        return import_athena(self.config, self.source, self.root / "work")

    def test_public_workflow_reads_athena_intermediates_without_arrow(self):
        config_path = self.root / "release.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {"sources": {"clinical": {**self.config, "format": "athena", "path": "input"}}}
            ),
            encoding="utf-8",
        )
        output = self.root / "dictionary"
        prepare(config_path, output, [], True)
        with duckdb.connect(str(output / "bundle" / "lexicon.duckdb"), read_only=True) as db:
            self.assertEqual(
                db.execute(
                    "SELECT concept_code FROM exact_names WHERE normalized_name = ?",
                    ['example "quoted"'],
                ).fetchall(),
                [("001",)],
            )
        declaration = yaml.safe_load((output / "vocabularies.yaml").read_text())
        self.assertEqual(
            {v["vocabulary_id"] for v in declaration["vocabularies"]},
            set(self.config["vocabularies"]),
        )

    def test_full_release(self):
        self.write(
            "CONCEPT_RELATIONSHIP",
            [
                "concept_id_1\tconcept_id_2\trelationship_id\tinvalid_reason",
                "2\t1\tRxNorm has ing\t",
                "3\t1\tConcept replaced by\t",
                "3\t4\tConcept replaced by\tD",
                "2\t5\tConsists of\tD",
                "2\t6\tConsists of\t",
                "5\t1\tMaps to\t",
                "5\t4\tMaps to\tD",
            ],
        )
        self.write(
            "CONCEPT_ANCESTOR",
            [
                "ancestor_concept_id\tdescendant_concept_id",
                "1\t2",
                "1\t1",
                "1\t5",
            ],
        )
        self.write(
            "CONCEPT_SYNONYM",
            [
                "concept_id\tconcept_synonym_name",
                "6\tOmitted",
                "5\tOther name",
                "5\tOther name",
            ],
        )
        output = self.run_import()
        concepts = self.read(output, "concepts")
        self.assertEqual(len(concepts), 5)
        self.assertIn(
            ("RxNorm", "001", 'Example "quoted"', "IN", "active", "1970-01-01", "2099-12-31", None),
            concepts,
        )
        self.assertIn(
            ("RxNorm", "002", "Old", "IN", "remapped", "1970-01-01", "2000-01-30", "001"), concepts
        )
        self.assertTrue(any(r[1:5] == ("003", "Older", "IN", "obsolete") for r in concepts))
        self.assertTrue(any(r[1:4] == ("005", "Example pack", "BPCK") for r in concepts))
        self.assertEqual(
            self.read(output, "synonyms"), [("SNOMED", "100", "Other name", "synonym", "SNOMED")]
        )
        self.assertEqual(
            self.read(output, "relationships"),
            [
                (
                    "RxNorm Extension",
                    "005",
                    "has_ingredient",
                    "RxNorm",
                    "001",
                    "OMOP CONCEPT_ANCESTOR; OMOP RxNorm has ing",
                ),
                ("SNOMED", "100", "maps_to", "RxNorm", "001", "OMOP Maps to"),
            ],
        )
        keys = self.read(output, "xrefs")
        self.assertIn(("SNOMED", "100", "OMOP_STANDARD", "1", "OMOP"), keys)
        self.assertIn(("RxNorm", "001", "OMOP_CONCEPT_ID", "1", "OMOP"), keys)
        self.assertNotIn(("SNOMED", "100", "OMOP_STANDARD", "4", "OMOP"), keys)
        declaration = yaml.safe_load((output / "vocabularies.yaml").read_text())
        self.assertEqual(
            {(v["release"], v["license"]) for v in declaration["vocabularies"]},
            {("2026", "fixture"), ("3", "fixture"), ("999", "fixture")},
        )

    def test_brand_salt_and_quantified_concepts(self):
        self.write(
            "CONCEPT",
            [
                "concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\tstandard_concept\tconcept_code\tvalid_start_date\tvalid_end_date\tinvalid_reason",
                "1\tatorvastatin\tDrug\tRxNorm\tIngredient\tS\t83367\t19700101\t20991231\t",
                "2\tLipitor\tDrug\tRxNorm\tBrand Name\t\t153165\t19700101\t20991231\t",
                "3\tatorvastatin calcium\tDrug\tRxNorm\tPrecise Ingredient\t\t83366\t19700101\t20991231\t",
                "4\t0.5 ML x 10 MG/ML Injection\tDrug\tRxNorm\tQuant Clinical Drug\tS\t900\t19700101\t20991231\t",
            ],
        )
        self.write(
            "CONCEPT_RELATIONSHIP",
            [
                "concept_id_1\tconcept_id_2\trelationship_id\tinvalid_reason",
                "2\t1\tBrand name of\t",
                "1\t2\tHas brand name\t",
                "3\t1\tForm of\t",
                "3\t1\tMaps to\t",
                "1\t3\tHas form\t",
            ],
        )
        self.config = {"vocabularies": ["RxNorm"]}
        self.write(
            "VOCABULARY",
            ["vocabulary_id\tvocabulary_name\tvocabulary_version", "RxNorm\tRxNorm\t2026"],
        )
        output = self.run_import()
        edges = {r[:5] for r in self.read(output, "relationships")}
        self.assertEqual(
            edges,
            {
                ("RxNorm", "153165", "brand_name_of", "RxNorm", "83367"),
                ("RxNorm", "83366", "form_of", "RxNorm", "83367"),
                ("RxNorm", "83366", "maps_to", "RxNorm", "83367"),
            },
        )
        classes = {r[1]: r[3] for r in self.read(output, "concepts")}
        self.assertEqual(classes["900"], "SCD")

        self.config["relationships"] = {"Brand name of": "brand_name_of"}
        output = import_athena(self.config, self.source, self.root / "work2")
        self.assertEqual({r[2] for r in self.read(output, "relationships")}, {"brand_name_of"})
        self.config["relationships"] = {"Brand name of": "Brand Name Of"}
        with self.assertRaisesRegex(ValueError, "lower_snake_case"):
            import_athena(self.config, self.source, self.root / "work3")

    def test_ambiguous_replacement_is_not_guessed(self):
        self.write(
            "CONCEPT_RELATIONSHIP",
            [
                "concept_id_1\tconcept_id_2\trelationship_id\tinvalid_reason",
                "3\t1\tConcept replaced by\t",
                "3\t4\tConcept replaced by\t",
            ],
        )
        output = self.run_import()
        self.assertIsNone(next(r for r in self.read(output, "concepts") if r[1] == "002")[-1])
        self.assertFalse(self.read(output, "synonyms"))
        self.assertFalse(self.read(output, "relationships"))

    def test_bad_selection_or_missing_columns_never_publish(self):
        self.config["vocabularies"].append("absent")
        with self.assertRaisesRegex(ValueError, "missing vocabulary"):
            self.run_import()
        self.config["vocabularies"].pop()
        self.config["domains"] = {"SNOMED": ["absent domain"]}
        with self.assertRaisesRegex(ValueError, "no Athena concepts"):
            self.run_import()
        self.write("CONCEPT_RELATIONSHIP", ["concept_id_1\tconcept_id_2"])
        with self.assertRaisesRegex(ValueError, "missing Athena column"):
            self.run_import()
        self.assertFalse((self.root / "work" / "athena_tables").exists())


if __name__ == "__main__":
    unittest.main()
