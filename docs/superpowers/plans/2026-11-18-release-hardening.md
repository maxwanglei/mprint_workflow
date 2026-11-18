# Release Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed defects from the 2026-11-18 review so that bad input produces a clean error, real releases import without silent data loss or duplication, and the output contract in FORMATS.md holds.

**Architecture:** Concentrate guards at the two boundaries. Config is validated before any reader runs, and reader output is normalized in one publish step that validates, deduplicates and writes every table with one CSV dialect shared with the bundle builder. Reader-specific fixes stay inside each reader module.

**Tech Stack:** Python 3.11+, DuckDB (`>=1.1,<2`), PyYAML, optional openpyxl, pytest, sqlite3 (stdlib, UMLS spill only).

**Spec:** The review list in the 2026-11-18 session. The verdict for each claim is below. Each verdict was reproduced with synthetic probes against this code, or measured on the real public releases listed. NCIt FLAT 2026-10, UNIIs.zip 2026-08-04, Drugs@FDA 2026-11-17, Orange Book 2026-11-13, EMA Article 57 Rev.91.

| Claim | Verdict | Evidence | Task |
| --- | --- | --- | --- |
| csv 128 KB field limit | Mechanism real, trigger not seen | Synthetic 140k field fails. Real max field is NCIt 8,246 and UNII 3,000 chars | 2 |
| Article 57 short row + `code_column` → "missing config key" | Confirmed | `missing config key 'marketing authorisation holder'`. Real XLSX rows are padded to 8 cells, so CSV/TSV exports only | 1, 5 |
| Any reader `KeyError` reported as config key | Confirmed | A zip named `.xlsx` gives `missing config key "There is no item named '[Content_Types].xml'"` | 1, 2 |
| Ragged `tables` row → `TypeError` | Confirmed | `TypeError: keywords must be strings` traceback | 3 |
| Truncated `.gz` / bad deflate escape | Confirmed | `EOFError` (mesh, umls) and `zlib.error` (gsrs) tracebacks | 2 |
| `sqlite3.Error` escapes | Confirmed by type hierarchy | not a subclass of any `INPUT_ERRORS` member | 2 |
| openpyxl `InvalidFileException` | Not reachable | suffix is already `.xlsx`. The real failure is the `KeyError` above | 2 |
| YAML `path: null`, `code_column: 123`, `encoding: 123` | Confirmed | `TypeError` / `AttributeError` tracebacks | 1 |
| One bad DrugBank `<unii>` aborts import | Confirmed, documented fail-fast | `DrugBank drug DB00002 has an invalid UNII` | 6 |
| `__MACOSX/._*.xml` breaks DrugBank zip | Confirmed | `expected one *.xml member, found 2` | 6 |
| FDA files are cp1252 | Not reproduced | both current files are valid UTF-8. `encoding` option already exists | 9 (test only) |
| Drugs@FDA type/holder in `Applications.txt` | Confirmed | `application_type`, `holder`, `route` blank on 52,021/52,021 rows | 9 |
| UMLS spills to system `/tmp` | Confirmed, worse than stated | `lsof` shows `etilqs_*` sort files in `$TMPDIR` even with the DB elsewhere. Only `PRAGMA temp_store_directory` moves them | 8 |
| Duplicate evidence rows | Confirmed for EMA, not FDA | EMA 2,387 / 164,784 identical rows. Orange Book and Drugs@FDA 0 | 4 |
| Duplicate synonym rows | Confirmed | UNII 2,019 identical rows. UMLS reproduced synthetically | 4 |
| Athena untrimmed synonyms | Confirmed in code | | 4 |
| Athena keeps synonyms equal to the name | Not a defect | FORMATS.md allows a preferred spelling as a typed atom (UMLS does the same) | none |
| Parquet non-string columns / duplicate headers | Confirmed | `7` accepted. `concept_name,concept_name` keeps the second | 3 |
| `check_keys` dialect differs from bundle | Confirmed, different failure | Not escape mis-sniffing. A comma or quote first appearing after ~20k unquoted rows fails `check_keys` ("Expected 4 Found 5"). The bundle reader would accept it | 4 |
| Uppercase status accepted | Confirmed | `ACTIVE` written to output | 4 |
| Unstripped fields | Partly | Real Orange Book has 65 padded strength/holder values. `"TABLET; ORAL"` never occurs (all 48,990 rows have exactly one `;`). NCIt has 0 padded codes | 4 |
| `path: ""` resolves to config dir | Confirmed in code | | 1 |
| Late metadata validation | Confirmed | `license: null` fails only after ingest | 1 |
| ncit `semantic_types: []` imports everything | Confirmed | 213,746 rows in the real release instead of 27,843 | 1 |
| mesh counts raw elements | Confirmed | supplemental file with 0 matches succeeds | 7 |
| Article 57 duplicate captions / legend row | Confirmed in code | real header is at row 20 with 8 distinct captions | 5 |
| UMLS 1-to-1 only within filtered atoms | Confirmed (documented) | code split across a T121 and a T028 CUI yields an `NCIt` xref | 8 |
| Windows rename onto empty dir; `--output .` | Confirmed (POSIX half measured) | scratch in `output.parent`, output inode replaced | 10 |
| Test coverage gaps | Confirmed | mesh 15%, substances 19%, products 38%. `mrsty`, `check_keys` rejections and `main()` unexecuted | all |
| `bundle.py` lacks `DUCKDB_CONFIG`; athena duplicates it; YAML escapes Unicode | Confirmed | | 4 |
| `name_source` varies by reader | Not a defect | blank falls back to `vocabulary_id` per FORMATS.md. Authorities differ by design | none |
| Athena language filter | Feature request | out of scope | none |
| Direct-script bootstrap breaks outside `mprint_workflow/` | Confirmed | unpacked sdist gives `ModuleNotFoundError`. The documented `pip install` + `mprint-prepare` path works | 11 |
| Package/CLI/import names differ; `.gitignore` in sdist; flat layout | Cosmetic | renaming `mprint_workflow` breaks the README API. Hatchling ships `.gitignore` by default | none |
| Workspace has build artifacts and no git | Confirmed | all already covered by `.gitignore` | 0, 11 |

## Global Constraints

- `requires-python = ">=3.11"`. Runtime dependencies stay `duckdb>=1.1,<2` and `PyYAML>=6,<7`. openpyxl stays optional (`excel` extra). No new runtime dependency.
- Install DuckDB inside the pin explicitly. An unpinned `uv pip install duckdb` resolves 2.0.0.
- Ruff line length 100, rules `E,F,I,B,UP`.
- Import package stays `mprint_workflow`. The public API in README (`prepare`, `bundle.normalize_name`) keeps its signatures.
- Output CSVs stay UTF-8, comma-delimited, with a header and standard double-quote escaping. Column order follows `schema.TableSpec.columns`. All values are text.
- `INPUT_ERRORS` remains the single tuple that `prepare` wraps and `main` reports.
- Every test calls the public entry (`prepare`, `main`, or `build_bundle`), not reader internals, except where a step says otherwise.

## Review Focus

1. **Real Windows.** Task 10 emulates Windows `rename` semantics. Nobody runs the suite on Windows. Expect `prepare` into an existing empty directory to succeed there.
2. **Licensed and large releases (UMLS, MeSH, DrugBank) never run end to end.** Task 8 changes UMLS xref output. Expect fewer native-code xrefs and no new ones. Task 4's synthetic UMLS dedupe test pins the duplicate case.
3. **Re-importing a pre-fix `tables` output.** Older outputs are unsorted and may contain duplicates. Expect both to import, dedupe and sort. Task 4 pins uppercase status as a rejection.
4. **Publish-step memory on very large tables.** `SELECT DISTINCT ... ORDER BY ALL` over tens of millions of synonyms must spill to the run's scratch directory, not RAM or `/tmp`. Task 4 keeps the publish connection file-backed under `scratch`.
5. **cp1252 FDA files.** Not seen in current releases. Expect `encoding: cp1252` to read them. Task 9 pins it.

---

### Task 0: Baseline

**Files:** none modified.

- [ ] **Step 1: Initialize version control**

Run: `git init && git add -A && git commit -m "chore: baseline before release hardening"`
Expected: `git status --short` prints nothing. `dist/`, `__pycache__/`, `.pytest_cache/` and `.ruff_cache/` are untracked because `.gitignore` already lists them.

- [ ] **Step 2: Create the dev environment**

Run: `uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python 'duckdb>=1.1,<2' 'PyYAML>=6,<7' openpyxl pytest ruff`
Expected: `.venv/bin/python -c "import duckdb; print(duckdb.__version__)"` prints a `1.x` version.

- [ ] **Step 3: Confirm the green baseline**

Run: `.venv/bin/python -m pytest -q`
Expected: `8 passed`. pytest imports the checkout as `mprint_workflow` because the root has `__init__.py`. Run every later test command from the project root.

---

### Task 1: Config boundary

**Files:**
- Modify: `prepare_dictionaries.py:52-63` (option tables), `prepare_dictionaries.py:206-286` (`prepare`)
- Modify: `ncit.py:9-11`
- Create: `conftest.py`, `test_config.py`
- Modify: `pyproject.toml` (wheel `exclude` adds `"/conftest.py"`)
- Modify: `FORMATS.md` (ncit `semantic_types` is a nonempty list when given)

**Interfaces:**
- Produces: `REQUIRED: dict[str, frozenset[str]]` (format → required options). Native formats also require `vocabulary_id` and `release`. `TEXT_OPTIONS: frozenset[str]` holds `path, mrconso, mrsty, descriptors, supplemental, encoding, sheet, code_column, vocabulary_id, license`. `validate_source(settings: object) -> str` returns the format name or raises `ValueError`.
- Produces (fixture in `conftest.py`): `run_source(settings: dict, files: dict[str, str | bytes] | None = None, *, build: bool = False, extra: dict[str, dict] | None = None) -> Path`. It writes `files` under `tmp_path` and a config with `{"src": settings, **(extra or {})}`. It runs `prepare(config, tmp_path / "out", [], build)` and returns the output path. Also the `read_rows` fixture, which returns `read(output: Path, table: str) -> list[dict[str, str]]`. Every test that reads output requests both fixtures by name.

- [ ] **Step 1: Write the failing tests** in `test_config.py`

```python
NCIT = {"format": "ncit", "vocabulary_id": "V", "release": "r", "path": "t.txt"}
ROW = "C1\tiri\t\tDrug A\t\t\t\tPharmacologic Substance\n"

@pytest.mark.parametrize("option, value", [("path", None), ("path", ""), ("path", 5)])
def test_path_must_be_nonempty_text(run_source, option, value):
    with pytest.raises(ValueError, match="source 'src': path must be a nonempty string"):
        run_source({**NCIT, option: value}, {"t.txt": ROW})

@pytest.mark.parametrize("settings", [
    {"format": "article57", "code_column": 123},
    {"format": "drugsfda", "encoding": 123},
])
def test_scalar_options_must_be_text(run_source, settings):
    with pytest.raises(ValueError, match="must be a nonempty string"):
        run_source({"vocabulary_id": "V", "release": "r", "path": "x.csv", **settings})

def test_missing_required_option_is_named(run_source):
    with pytest.raises(ValueError, match="source 'src': missing required option 'path'"):
        run_source({k: v for k, v in NCIT.items() if k != "path"})

def test_duplicate_vocabulary_fails_before_reading_the_second_source(run_source):
    with pytest.raises(ValueError, match="duplicate or whitespace-padded vocabulary ID"):
        run_source(NCIT, {"t.txt": ROW}, extra={"again": {**NCIT, "path": "absent.txt"}})

def test_null_license_fails_before_reading(run_source):
    with pytest.raises(ValueError, match="license must be a nonempty string"):
        run_source({**NCIT, "license": None, "path": "absent.txt"})

def test_ncit_empty_semantic_types_is_rejected(run_source):
    with pytest.raises(ValueError, match="semantic_types must be a nonempty list"):
        run_source({**NCIT, "semantic_types": []}, {"t.txt": ROW})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest test_config.py -q`
Expected: FAIL. The null/numeric cases raise `TypeError`/`AttributeError`, the missing option says `missing config key`, the license and duplicate cases raise `FileNotFoundError`, and `semantic_types: []` succeeds.

- [ ] **Step 3: Implement `validate_source` and wire it into `prepare`**

`validate_source` checks the mapping shape, `format`, unknown options (existing logic moved out of `prepare`), `REQUIRED`, and that every present `TEXT_OPTIONS` value passes `schema._text`. In `prepare`, call it first inside the per-source `try`. Delete the `except KeyError` clause. Right after `vocabularies.append(...)` or `.extend(...)`, and before the reader or `copy_tables` runs, call `validate_declaration({"vocabularies": vocabularies, "source_aliases": aliases})`. In `ncit.py`, raise `ValueError("semantic_types must be a nonempty list of NCI semantic type names")` when the key is present and the list is empty.

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add prepare_dictionaries.py ncit.py conftest.py test_config.py pyproject.toml FORMATS.md
git commit -m "fix: validate source config before reading any release"
```

---

### Task 2: Input failures map to clean errors

**Files:**
- Modify: `prepare_dictionaries.py:64-71` (`INPUT_ERRORS`), start of `prepare`
- Modify: `products.py:125` (`_article_rows`)
- Create: `test_inputs.py`

**Interfaces:**
- Consumes: `run_source` from Task 1.
- Produces: `INPUT_ERRORS` gains `EOFError`, `zlib.error`, `sqlite3.Error`.

- [ ] **Step 1: Write the failing tests** in `test_inputs.py`

```python
def truncated_gzip(payload: bytes) -> bytes:
    data = gzip.compress(payload)
    return data[: len(data) // 2]

def test_truncated_mesh_gzip(run_source):
    # 4,000 complete DescriptorRecords so the cut lands inside the stream
    with pytest.raises(ValueError, match="source 'src'.*Compressed file ended"):
        run_source({..., "descriptors": "d.xml.gz"}, {"d.xml.gz": truncated_gzip(MESH_XML)})

def test_truncated_umls_gzip(run_source): ...           # same assertion, MRCONSO x5000

def test_corrupt_gsrs_deflate(run_source):
    # set the first deflate byte (offset 30 + len(member name)) to 0xFF
    with pytest.raises(ValueError, match="source 'src'.*invalid block type"): ...

def test_xlsx_that_is_not_a_workbook(run_source):
    with pytest.raises(ValueError, match="not an Excel workbook") as info: ...
    assert "missing config key" not in str(info.value)

def test_unselected_ncit_row_over_128k(run_source):
    big = "x" * 140_000   # in the definition column of a Gene row
    out = run_source({**NCIT, "semantic_types": ["Pharmacologic Substance"]}, {"t.txt": ROW + gene_row(big)})
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["C1"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest test_inputs.py -q`
Expected: FAIL with `EOFError`, `zlib.error`, `missing config key "There is no item named...`, and `field larger than field limit (131072)`.

- [ ] **Step 3: Implement**

Extend `INPUT_ERRORS`. Call `csv.field_size_limit(2**31 - 1)` at the top of `prepare` (not `sys.maxsize`, which overflows a C long on Windows). In `_article_rows`, wrap `load_workbook(...)` so a `KeyError` becomes `ValueError(f"{path}: not an Excel workbook ({exc})")`.

- [ ] **Step 4: Run the suite.** `.venv/bin/python -m pytest -q`. Expected: all pass.

- [ ] **Step 5: Commit** `git commit -am "fix: report corrupt archives and oversized fields as input errors"` (add `test_inputs.py` first).

---

### Task 3: `tables` input contract

**Files:**
- Modify: `prepare_dictionaries.py:114-138` (`copy_tables`)
- Modify: `test_inputs.py`

**Interfaces:** `copy_tables(directory: Path, sink: TableSink) -> None` is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_ragged_tables_row(run_source):        # "V,1,a,EXTRA" under a 3-column header
    with pytest.raises(ValueError, match=r"concepts\.csv:2: more fields than the header"): ...

def test_duplicate_tables_header(run_source):  # concept_name twice
    with pytest.raises(ValueError, match="duplicate column"): ...

def test_parquet_identifiers_must_be_text(run_source):  # 7::INTEGER AS concept_code
    with pytest.raises(ValueError, match=r"non-text column\(s\) \['concept_code'\]"): ...
```

- [ ] **Step 2: Run them to verify they fail.** Expected: `TypeError: keywords must be strings`, and the other two succeed silently.

- [ ] **Step 3: Implement.** Reject `len(columns) != len(set(columns))`. For Parquet, compare `relation.types` to `VARCHAR` before fetching. For CSV/TSV rows, `None in row` means extra fields, so raise with `reader.line_num`.

- [ ] **Step 4: Run the suite.** Expected: all pass.

- [ ] **Step 5: Commit** `git commit -am "fix: enforce the tables input contract"`

---

### Task 4: One publish step validates, normalizes and deduplicates every table

**Files:**
- Modify: `schema.py` (add `read_table_csv`)
- Modify: `prepare_dictionaries.py` (`TableSink.add` strips text, sink writes to `scratch / "raw"`, `check_keys` becomes `publish_tables`, `yaml.safe_dump(..., allow_unicode=True)`)
- Modify: `bundle.py:30-40` (use `read_table_csv`, connect with `config=DUCKDB_CONFIG`)
- Modify: `athena.py:111-114` (import `DUCKDB_CONFIG`)
- Create: `test_publish.py`
- Modify: `FORMATS.md` (output rows are distinct and sorted. `status` must match an accepted lowercase value exactly)

**Interfaces:**
- Produces: `schema.read_table_csv(con: duckdb.DuckDBPyConnection, path: Path, spec: TableSpec) -> duckdb.DuckDBPyRelation`. It reads with `columns=dict.fromkeys(spec.columns, "VARCHAR")`, `header=True`, `delimiter=","`, `quotechar='"'`, `escapechar='"'`, `auto_detect=False`. This is the only CSV dialect used to read prepared tables.
- Produces: `publish_tables(raw: Path, staging: Path, declaration: dict, scratch: Path) -> Counter[str]`. It runs the existing key, date, status, vocabulary and reference checks over `raw`, with status compared without `lower()`. It then writes each present table to `staging`, with `concepts` as `ORDER BY ALL` and the others as `SELECT DISTINCT * ... ORDER BY ALL`, using `COPY ... (HEADER, DELIMITER ',', QUOTE '"', ESCAPE '"')`. It returns written row counts. The DuckDB connection stays file-backed at `scratch / "checks.duckdb"` so large sorts spill under `scratch`.
- Changes: `prepare` returns `publish_tables`'s counts, not `sink.counts`.

- [ ] **Step 1: Write the failing tests** in `test_publish.py`

```python
def test_identical_umls_atoms_write_one_synonym(run_source):
    # two AUIs share (CUI, STR, SAB, TTY) with different CODE
    rows = [r["synonym"] for r in read_rows(out, "synonyms")]
    assert rows.count("Alias") == 1

def test_identical_evidence_rows_write_once(run_source):
    # Orange Book rows identical except TE_Code AB1/AB2
    assert len(read_rows(out, "product_evidence")) == 1

def test_text_fields_are_trimmed(run_source):
    # Strength "5MG ", Applicant_Full_Name " Example firm"
    assert (row["strength"], row["holder"]) == ("5MG", "Example firm")

def test_uppercase_status_rejected(run_source):     # tables input, status ACTIVE
    with pytest.raises(ValueError, match="invalid status or date"): ...

def test_quoted_name_after_sniff_window_validates(run_source):
    # tables input: 30,000 plain rows, then 'Example "quoted", salt'
    out = run_source(..., build=True)
    assert lookup(out, "x1") == 'Example "quoted", salt'

@pytest.mark.parametrize("table, header, row", [
    ("synonyms", ..., "V,absent,alias"),
    ("xrefs", ..., "V,absent,UNII,ABCDEFGHIJ"),
    ("relationships", ..., "V,1,has_ingredient,V,absent"),
    ("product_evidence", ..., "V,absent,Name"),
])
def test_dangling_reference_rejected(run_source, table, header, row):
    with pytest.raises(ValueError, match=f"{table}: missing concept"): ...

def test_bad_date_and_missing_replacement_rejected(run_source): ...  # "invalid status or date", "replacement refers"

def test_vocabularies_yaml_keeps_unicode(run_source):  # license "Lizenz für Forschung"
    assert "für" in (out / "vocabularies.yaml").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run them to verify they fail.** Expected: the duplicate, trim, status, late-quote (`Expected Number of Columns: 3 Found: 4`) and Unicode tests fail. The dangling-reference tests pass already and pin existing behavior.

- [ ] **Step 3: Implement** `read_table_csv`, then `publish_tables`, then switch `bundle.py` to `read_table_csv(...).create(table)` and `DUCKDB_CONFIG`. Delete `check_keys`, which has one caller.

- [ ] **Step 4: Run the suite.** Expected: all pass, including the existing Unicode-alias and FDA tests.

- [ ] **Step 5: Commit** `git commit -am "fix: publish tables through one validated, deduplicated CSV dialect"`

---

### Task 5: Article 57 reader

**Files:** Modify `products.py:146-240`. Create `test_readers.py`. Modify `FORMATS.md` (duplicate captions are an error, and the header scan skips rows lacking the required captions).

- [ ] **Step 1: Write the failing tests** in `test_readers.py`

```python
HEADER = "Product name,Local code,Active substance,Route of administration,Product authorisation country,Marketing authorisation holder\n"

def test_short_row_with_code_column_keeps_blank_holder(run_source):
    out = run_source({**A57, "code_column": "Local code"}, {"a.csv": HEADER + "Drug A,123,sub,oral,France\n"})
    [row] = read_rows(out, "product_evidence")
    assert (row["concept_code"], row["holder"]) == ("national:France:123", "")

def test_header_found_after_legend_row(run_source):   # "Legend,Product name\n" precedes HEADER
    assert len(read_rows(out, "concepts")) == 1

def test_duplicate_caption_rejected(run_source):      # "Strength" twice
    with pytest.raises(ValueError, match="duplicate Article 57 column"): ...

def test_xlsx_with_title_rows(run_source, tmp_path):  # pytest.importorskip("openpyxl"); header on row 3
    assert read_rows(out, "concepts")[0]["concept_name"] == "Drug A"
```

- [ ] **Step 2: Run them to verify they fail.** Expected: `missing config key 'marketing authorisation holder'`, `missing Article 57 column(s)`, and a silent pass for the duplicate caption.

- [ ] **Step 3: Implement.** Pad short rows to the header length with `""`. Raise on duplicate nonblank captions. When a candidate row contains `product name` but lacks required captions, remember its missing set and keep scanning. Only if no header is found by end of file, raise with that missing set.

- [ ] **Step 4: Run the suite.** **Step 5: Commit** `git commit -am "fix: Article 57 short rows, legend rows and duplicate captions"`

---

### Task 6: DrugBank and archive member selection

**Files:** Modify `substances.py:17-33, 84-150`. Modify `test_readers.py`, `FORMATS.md` (an invalid direct `unii` drops that cross-reference and is counted on stderr).

**Interfaces:** `_drugbank_record(drug, vocabulary, sink) -> bool` returns `True` when it dropped an invalid UNII. `read_drugbank` prints `f"{path}: skipped {n} invalid UNII value(s)"` to stderr when `n` is nonzero.

- [ ] **Step 1: Write the failing tests**

```python
def test_drugbank_zip_ignores_macos_metadata(run_source):   # adds "__MACOSX/._full database.xml"
    assert len(read_rows(out, "concepts")) == 1

def test_invalid_unii_drops_only_that_link(run_source, capsys):  # second drug has "abcdefghij"
    assert len(read_rows(out, "concepts")) == 2
    assert [r["key_value"] for r in read_rows(out, "xrefs")] == ["ABCDEFGHIJ"]
    assert "skipped 1 invalid UNII" in capsys.readouterr().err

def test_gsrs_zip_with_readme_member(run_source):  # mirrors UNIIs.zip 2026-08: "READ ME UNII Lists.txt" beside the names file
    assert {r["concept_code"] for r in read_rows(out, "concepts")} == {"ABCDEFGHIJ"}
```

- [ ] **Step 2: Run them to verify they fail.** Expected: `found 2` and `has an invalid UNII`. The GSRS test passes already and adds coverage.

- [ ] **Step 3: Implement.** Skip archive members whose path has a `__MACOSX` component or whose basename starts with `._`. Replace the UNII `raise` with a skip that returns `True`.

- [ ] **Step 4: Run the suite.** **Step 5: Commit** `git commit -am "fix: DrugBank tolerates macOS zip metadata and bad UNII links"`

---

### Task 7: MeSH match accounting

**Files:** Modify `mesh.py:100-123`, `test_readers.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_mesh_file_with_no_matching_records(run_source):  # descriptors match, SCRClass="2" only with scr_classes [1]
    with pytest.raises(ValueError, match="no SupplementalRecord records match"): ...

def test_mesh_descriptor_aliases_and_unii(run_source):    # .gz input, permuted term excluded, RegistryNumber ABCDEFGHIJ
    assert synonyms == ["Alias"]
    assert ("UNII", "ABCDEFGHIJ") in xrefs
```

- [ ] **Step 2: Run them to verify they fail.** Expected: the first succeeds silently.

- [ ] **Step 3: Implement.** Count only included records. Raise `f"{path}: no {tag} records match the configured filters"` when a configured file contributes none.

- [ ] **Step 4: Run the suite.** **Step 5: Commit** `git commit -am "fix: MeSH reports a configured file that matches nothing"`

---

### Task 8: UMLS spill location and code cardinality

**Files:**
- Modify: `prepare_dictionaries.py` (`TableSink.__init__(self, source_root, destination, stack, scratch)` stores `self.scratch`, and `prepare` passes its scratch path)
- Modify: `umls.py:94-170`
- Modify: `test_readers.py`, `FORMATS.md` (temporary storage lives beside the output. One-to-one is evaluated over all unsuppressed atoms of the selected sources and language, before `ttys` and semantic-type filters)

**Interfaces:** `TableSink.scratch: Path`, used by `read_umls`.

- [ ] **Step 1: Write the failing tests**

```python
def test_umls_spills_under_run_scratch(run_source, monkeypatch, tmp_path):
    # spy umls.TemporaryDirectory and record kwargs["dir"]
    assert seen and Path(seen[0]).is_relative_to(tmp_path)

def test_code_split_outside_the_filter_is_not_one_to_one(run_source):
    # C1 (T121) and C2 (T028) both carry NCI code C9. semantic_types [T121]
    assert ("NCIt", "C9") not in {(r["key_type"], r["key_value"]) for r in read_rows(out, "xrefs")}

def test_semantic_type_filter_selects_cuis(run_source):
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["C1"]
```

- [ ] **Step 2: Run them to verify they fail.** Expected: `seen == [None]`, and the `NCIt C9` xref is present.

- [ ] **Step 3: Implement.** Use `TemporaryDirectory(dir=sink.scratch)`. Then run `PRAGMA temp_store_directory = '<that dir>'`, doubling any `'` in the path. The lsof check showed that without it SQLite's `etilqs_*` sort files go to `$TMPDIR` regardless of the database location. Store every language/SAB/SUPPRESS-eligible atom with a `kept` flag (TTY filter and semantic-type eligibility). Names and concepts read `WHERE kept`, and the cardinality CTE reads all rows and then restricts output to kept CUIs.

- [ ] **Step 4: Verify the spill location on a large run**

Generate a 3M-line synthetic MRCONSO, run `prepare` in the background, and run `lsof -p <pid> | grep etilqs` during the sort.
Expected: every `etilqs_*` path is under the output's parent `.mprint-*` directory, none under `$TMPDIR`.

- [ ] **Step 5: Run the suite and commit** `git commit -am "fix: UMLS spills beside the output and checks code cardinality globally"`

---

### Task 9: Drugs@FDA application facts and route

**Files:** Modify `products.py:22-114`, `prepare_dictionaries.py` (`OPTIONS["drugsfda"]` and `TEXT_OPTIONS` gain `applications`), `sources.example.yaml` (`applications: raw/drugsfda/Applications.txt`), `FORMATS.md` (drugsfda row), `test_readers.py`.

**Interfaces:** `_application_facts(path: Path, encoding: str) -> dict[str, tuple[str, str]]` maps `ApplNo` to `(ApplType, SponsorName)` from the tab-delimited, `QUOTE_NONE` `Applications.txt`, and raises `ValueError` naming missing columns.

- [ ] **Step 1: Write the failing tests**

```python
def test_drugsfda_joins_applications(run_source):
    assert (row["application_type"], row["holder"]) == ("NDA", "EXAMPLE PHARMA")

def test_drugsfda_splits_a_single_semicolon_form(run_source):  # "TABLET;ORAL" and "KIT"
    assert forms == {("TABLET", "ORAL"), ("KIT", "")}

def test_drugsfda_reads_cp1252(run_source):  # encoding: cp1252, DrugName bytes b"EXAMPLE\xae"
    assert read_rows(out, "concepts")[0]["concept_name"] == "EXAMPLE®"
```

- [ ] **Step 2: Run them to verify they fail.** Expected: unknown option `applications`, and the form is unsplit. The cp1252 test passes already and pins Review Focus 5.

- [ ] **Step 3: Implement.** Apply the Orange Book rule to both formats: exactly one `;` splits form and route (51,629 of 52,021 current Drugs@FDA rows). Without `applications`, `application_type` and `holder` stay blank. Delete the `Appl_Type`/`Applicant` lookups for Drugs@FDA rows, since `Products.txt` never has those columns.

- [ ] **Step 4: Run the suite.** **Step 5: Commit** `git commit -am "feat: Drugs@FDA evidence carries application type, sponsor and route"`

---

### Task 10: Output directory publication

**Files:** Modify `prepare_dictionaries.py:218-285`, `bundle.py:21-98`, `test_publish.py`. Modify `README.md` (`--output` must be a new or empty directory other than the working directory).

- [ ] **Step 1: Write the failing tests**

```python
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

def test_output_cannot_be_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="current directory"):
        prepare(DEMO, Path.cwd(), [], False)
```

- [ ] **Step 2: Run them to verify they fail.** Expected: `FileExistsError`, and the second succeeds.

- [ ] **Step 3: Implement.** Immediately before each `staging.rename(output)`, `output.rmdir()` if it exists. `prepare` rejects an `output` where `Path.cwd().is_relative_to(output)`.

- [ ] **Step 4: Run the suite.** **Step 5: Commit** `git commit -am "fix: publish into an existing empty directory on Windows"`

---

### Task 11: Packaging, CLI and release check

**Files:** Modify `prepare_dictionaries.py:18-21` (delete the direct-script bootstrap) and `test_prepare_dictionaries.py` (CLI tests).

- [ ] **Step 1: Write the CLI tests**

```python
def test_cli_writes_demo(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["mprint-prepare", "--config", str(DEMO), "--output", str(tmp_path / "o"), "--build"])
    main()
    assert "concepts.csv: 3 rows" in capsys.readouterr().out

def test_cli_reports_bad_config(tmp_path, monkeypatch, capsys):
    # config with sources: {} ; expect SystemExit code 1 and "error:" on stderr
```

- [ ] **Step 2: Delete the bootstrap and run the suite.** Expected: all pass.

- [ ] **Step 3: Lint.** `.venv/bin/ruff check . && .venv/bin/ruff format --check .`. Expected: no findings.

- [ ] **Step 4: Rebuild and verify the documented install path**

Run: `rm -rf dist && uvx --from build pyproject-build`. Then, in a fresh directory, unpack `dist/*.tar.gz` and run `uv venv -p 3.12 v && uv pip install -p v/bin/python '.[test]' 'duckdb<2' && v/bin/python -m pytest -q && v/bin/mprint-prepare --config demo/sources.yaml --output d --build`.
Expected: tests pass, `Wrote .../d`, and `unzip -l dist/*.whl` lists no `test_*.py` or `conftest.py`.

- [ ] **Step 5: Real-release smoke run.** Download the four public releases in the Spec table into empty directories and run `prepare` with `--only` per source and `--build`. Expected: concept counts match the baseline (NCIt 27,843, UNII 171,917, Drugs@FDA 52,021, Orange Book 48,990, EMA 162,397). Synonym and evidence rows show no byte-identical duplicates. Drugs@FDA evidence has nonblank `holder` when `applications` is set.

- [ ] **Step 6: Commit** `git commit -am "chore: drop direct-script bootstrap, cover the CLI"`
