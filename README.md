# MPRINT drug dictionaries

Combine local drug vocabulary releases into UTF-8 CSV tables for use in any application. For MPRINT, those tables support exact-name lookup of reported FAERS drug names, terms extracted from PubMed text and MeSH heading labels assigned to articles. Add `--build` to get a DuckDB database indexed by name. Each hit keeps the vocabulary, native code, matched wording and original source of the name. A shared name can return several substances or products.

## Run the included demo

The `mprint_workflow` directory is a standalone Python project. Copy the directory or unpack the source distribution and open a shell in the folder containing its `pyproject.toml`. Use Python 3.11 or later. The base install includes DuckDB and PyYAML. The demo uses invented NCIt and UMLS records and runs without downloading terminology.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install .
mprint-prepare --config demo/sources.yaml --output data/demo --build
```

For Article 57 Excel input, install `python -m pip install '.[excel]'`. CSV and TSV worksheet exports work with the base install. To run the project's tests from this directory, use `python -m pip install '.[test]'` and `python -m pytest`.

The demo produces three concepts, including two records named `Example compound` and a separate NCIt record for `Example salt`. The directory layout after the command is shown below.

```text
pyproject.toml             # standalone package and command definition
FORMATS.md                 # reader configuration and output columns
demo/
  sources.yaml             # references the two files beside it
  Thesaurus.txt
  MRCONSO.RRF
sources.example.yaml       # configuration template for real releases
data/demo/
  concepts.csv
  synonyms.csv
  relationships.csv
  xrefs.csv
  vocabularies.yaml
  bundle/                  # created by --build
    lexicon.duckdb
    manifest.json
```

Runs with FDA or EMA product evidence also write `product_evidence.csv`. The four core CSVs always have headers, including when a table has zero rows.

## Look up a reported name

Run this Python snippet from the same folder after the demo. It uses the installed DuckDB library and binds the search string as a parameter. `normalize_name` collapses whitespace and case-folds Unicode text. It retains punctuation, word order, salt, strength and dose-form wording.

```python
import duckdb
from mprint_workflow.bundle import normalize_name

with duckdb.connect("data/demo/bundle/lexicon.duckdb", read_only=True) as db:
    rows = db.execute(
        """
        SELECT n.vocabulary_id, n.concept_code, c.concept_name, n.matched_name, n.source
        FROM exact_names AS n
        JOIN concepts AS c USING (vocabulary_id, concept_code)
        WHERE n.normalized_name = ?
        ORDER BY n.vocabulary_id, n.concept_code
        """,
        [normalize_name(" EXAMPLE   drug ")],
    ).fetchall()
    for row in rows:
        print(row)
```

```text
('NCIt', 'C99900001', 'Example compound', 'Example drug', 'NCIt')
('UMLS', 'C9900001', 'Example compound', 'Example drug', 'NCIt')
```

These two hits trace to one naming authority. The UMLS source atom uses the NCI abbreviation, which the demo maps to `NCIt`. To count independent name assertions, group by the resolved `source`. To look up the original atom type or attach a regulator's product details, follow the joins in the [format reference](FORMATS.md#output-tables).

## Prepare a release

1. Copy `sources.example.yaml` next to your release directory. Edit the paths and filters for the sources you have. An absolute input path works anywhere. A relative input path starts at the directory containing the YAML file. `--output` is relative to the shell's working directory unless you pass an absolute path.
2. Give each native source its actual `release` identifier or snapshot date and its `vocabulary_id`. Quote dates and numeric-looking identifiers. Record its license in `license` for downstream users. Athena reads release versions from `VOCABULARY.csv`. Prepared tables carry their own `vocabularies.yaml`.
3. Choose a fresh output directory. It may exist if it is empty, but it cannot be the current working directory or one of its parents. `--only` takes one or more **source names**, which are the keys directly under `sources` in YAML. For example, with the template copied to `data/sources.yaml` and the selected files installed under `data/raw/`, run the following.

```bash
mprint-prepare --config data/sources.yaml --only athena gsrs \
  --output data/athena-gsrs --build
```

Omit `--only` to import all configured sources. Omit `--build` if you only need the CSVs and declaration. The command checks concept keys, lifecycle values, dates, vocabulary declarations and references to retained concepts before publishing the output. Use a different output directory for a new selection or release. To compile existing tables, point a `format: tables` source to their directory and rerun with `--build` and a fresh output path.

You can also run preparation from Python. For example, use the installed package's demo data to build the NCIt subset in the current working directory.

```python
from importlib.resources import files
from pathlib import Path
from mprint_workflow.prepare_dictionaries import prepare

config = Path(str(files("mprint_workflow").joinpath("demo", "sources.yaml")))
counts = prepare(config, Path("data/ncit-only"), only=["ncit"], compile_bundle=False)
print(counts["concepts"])  # 2
```

The reference lists the release files to obtain for each dictionary, accepted filters, exact table schemas, and code and provenance conventions. [Input and output formats](FORMATS.md)
