# MPRINT dictionary formats

Use this reference when assembling a release or writing a consumer for the output. The [quick start](README.md) builds a working dictionary with three invented records.

## Configuration

The root YAML mapping requires `sources`. Its keys are local names you choose and pass to `--only`. Each selected source requires a `format`. Optional top-level `source_aliases` maps an original name authority to a canonical spelling, for example `NCI: NCIt` or `MSH: MeSH`.

```yaml
source_aliases: {NCI: NCIt}
sources:
  nci_release:
    format: ncit
    vocabulary_id: NCIt
    release: "26.10d"
    license: "record the terms for your release here"
    path: raw/ncit/Thesaurus.txt
    semantic_types: [Pharmacologic Substance, Clinical Drug]
```

For `umls`, `mesh`, `gsrs`, `ncit`, `drugbank`, `drugsfda`, `orange_book` and `article57`, set `vocabulary_id` and `release`. Optional `license` defaults to `unspecified`. Use names such as `RxNorm`, `NCIt` and `UNII` when relying on cross-references carrying those namespaces. `athena` and `tables` obtain vocabulary IDs and releases from their input metadata. Input paths resolve relative to the YAML, or may be absolute. Each selected source must contribute concepts. Declare each vocabulary once per run. Keep the release files and configuration if you need to recreate the selection.

## Native release inputs

### `athena` — RxNorm, RxNorm Extension, SNOMED CT and ATC

Set `path` to an extracted Athena download and `vocabularies` to a nonempty list of Athena vocabulary IDs, for example `[RxNorm, RxNorm Extension, SNOMED, ATC]`. Optional `domains` maps a selected ID to a nonempty list, for example `{SNOMED: [Drug]}`. Optional `license` applies to the selected vocabularies. `VOCABULARY.csv` supplies each vocabulary's release version. A blank version is recorded as `unversioned`. [OHDSI Athena](https://athena.ohdsi.org/) · [OMOP vocabulary fields](https://ohdsi.github.io/CommonDataModel/cdm54.html)

The native files are UTF-8 tab-delimited text with headers and literal quotation marks. Their `.csv` filenames are significant. Extra release columns are permitted.

| File | Required columns | Use |
| --- | --- | --- |
| `CONCEPT.csv` | `concept_id`, `vocabulary_id`, `concept_code`, `concept_name`, `concept_class_id`, `standard_concept`, `valid_start_date`, `valid_end_date`, `invalid_reason` | Required. Also supply `domain_id` when filtering domains. `concept_code` is the native vocabulary code. OMOP `concept_id` identifies the row across Athena tables. Native dates use `YYYYMMDD`. |
| `VOCABULARY.csv` | `vocabulary_id`, `vocabulary_name`, `vocabulary_version` | Required. One entry for each selected vocabulary. |
| `CONCEPT_SYNONYM.csv` | `concept_id`, `concept_synonym_name` | Optional. Imports names of selected concepts across the supplied languages. |
| `CONCEPT_RELATIONSHIP.csv` | `concept_id_1`, `concept_id_2`, `relationship_id`, `invalid_reason` | Optional. Supplies typed edges, OMOP mappings and same-vocabulary replacement evidence. |
| `CONCEPT_ANCESTOR.csv` | `ancestor_concept_id`, `descendant_concept_id` | Optional. Supplies `has_ingredient` edges from retained RxNorm-family descendants to their retained ingredient ancestors. |

OMOP invalid reasons `D` and `U` become `obsolete` and `remapped`. The reader emits `replaced_by` for an unambiguous current same-vocabulary `Concept replaced by` relationship. Eligible direct edges preserve direction. The native relations `RxNorm has ing`, `Has precise ing`, `Has tradename`, `RxNorm has dose form` and `Consists of` become `has_ingredient`, `has_precise_ingredient`, `has_tradename`, `has_dose_form` and `consists_of`, respectively. Both endpoints of an output edge must be retained.

### `umls` — UMLS Metathesaurus

Required `mrconso` points to `META/MRCONSO.RRF` and `sources` is a nonempty list of source abbreviations, for example `[RXNORM, MSH, NCI, SNOMEDCT_US, DRUGBANK]`. Optional `language` is a three-letter uppercase `LAT` value (default `ENG`). Optional `ttys` is a nonempty list of allowed term types across the selected sources. For a drug-oriented selection, the example uses RxNorm `IN`, `PIN`, `MIN`, `BN`, `SCD`, `SBD`, MeSH `MH`, `NM`, and `PT`, `SY` where the selected source uses them. Check the term types in your release before expanding the set. [NLM MRCONSO fields](https://www.ncbi.nlm.nih.gov/books/NBK9685/table/ch03.T.concept_names_and_sources_file_mr/)

To select CUIs by semantic type, supply both `mrsty` (path to `META/MRSTY.RRF`) and `semantic_types` (a nonempty list of TUI strings such as `[T121, T195, T200]`). A CUI qualifies if it has any selected TUI. Without this pair the reader uses the atom filters alone. Temporary SQLite storage holds eligible CUIs and atoms during preparation. [NLM MRSTY fields](https://www.ncbi.nlm.nih.gov/books/NBK9685/table/ch03.Tf/)

Both files accept plain UTF-8 or `.gz`, have no header, and require a trailing `|` on each row. Keep empty fields in position. The layouts in native order are

```text
CUI|LAT|TS|LUI|STT|SUI|ISPREF|AUI|SAUI|SCUI|SDUI|SAB|TTY|CODE|STR|SRL|SUPPRESS|CVF|
CUI|TUI|STN|STY|ATUI|CVF|
```

Only atoms with `SUPPRESS=N` enter the dictionary. For each retained CUI the reader prioritizes `TS=P`, `STT=PF` and `ISPREF=Y` in that order, then breaks ties by name, source abbreviation and term type. Each eligible atom contributes its wording, original SAB and TTY. Native-code cross-references require one-to-one correspondence within the selected atoms and one of the supported source namespaces (`RxNorm`, `MeSH`, `NCIt`, `SNOMED`, `DrugBank`). For MeSH, only `MH` descriptor headings and `NM` supplementary record names qualify for these links. All retained CUIs also carry an explicit `UMLS_CUI` key.

### `mesh` — MeSH descriptors and supplementary concept records

Supply `descriptors` or `supplemental` or both. Each is an NLM release XML file, plain or `.gz`. Optional `descriptor_tree_prefixes`, for example `[D]` or `[D03]`, retains descriptors with a matching `TreeNumberList/TreeNumber` prefix. Optional `scr_classes`, for example `[1]`, selects `SupplementalRecord/@SCRClass` from classes 1–6. The example selects the chemicals/drugs tree and chemical supplementary records. [MeSH downloads](https://www.nlm.nih.gov/databases/download/mesh.html) · [MeSH XML elements](https://www.nlm.nih.gov/mesh/xml_data_elements.html)

| Input | XML path to record | Identifier and label within each record |
| --- | --- | --- |
| `descriptors` | `DescriptorRecordSet/DescriptorRecord` | `DescriptorUI`, `DescriptorName/String` |
| `supplemental` | `SupplementalRecordSet/SupplementalRecord` | `SupplementalRecordUI`, `SupplementalRecordName/String` |

Each selected record needs exactly one `ConceptList/Concept` with `PreferredConceptYN="Y"`. Its `TermList/Term/String` entries supply aliases, excluding `IsPermutedTermYN="Y"`. `LexicalTag` supplies the alias type when present. A 10-character uppercase alphanumeric `RegistryNumber` on the preferred concept supplies a UNII key. Other concepts, related registry numbers, mapped headings and pharmacological actions do not establish a name or identity for the preferred concept. MeSH headings assigned to individual PubMed articles belong with your query evidence.

### `gsrs` — FDA UNII names

Set `path` to FDA `UNIIs.zip` with exactly one member whose filename matches `unii*names*.txt` case-insensitively, or to the extracted names file. This is an unquoted UTF-8 TSV with header fields `UNII`, `DISPLAY_NAME`, `NAME`, `TYPE`. Each UNII is ten uppercase letters/digits and needs one consistent display name. `DISPLAY_NAME` supplies the preferred name. `of`, `sys` and `cn` name types supply official, systematic and common aliases. With `vocabulary_id: UNII` the UNII is the native concept code. [FDA UNII names release](https://www.fda.gov/industry/structured-product-labeling-resources/uniis-preferred-substance-names-and-their-identified-synonyms)

### `ncit` — National Cancer Institute Thesaurus

Set `path` to the extracted `Thesaurus.txt` from the NCI FLAT release. The headerless, unquoted UTF-8 TSV contains the following fields in order.

```text
code   concept IRI   parents   synonyms   definition   display name   concept status   semantic type   [concept in subset]
```

Use tab separators and keep empty fields in position. The ninth field is optional. Entries within a multivalued field use `|`. The first nonempty synonym is the preferred name, and later distinct names are aliases. Optional `semantic_types` is a nonempty list of native semantic type names. The reader retains a row if any match. Retired/obsolete source statuses become `obsolete`. [NCI FLAT release layout](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/ReadMe.txt)

### `drugbank` — DrugBank full database

Set `path` to the full XML database or a ZIP with a single `.xml` member. The root is `drugbank`. Every direct `drug` child must have exactly one direct `drugbank-id` marked `primary="true"` or `primary="1"` and a `name`. Optional `drug/@type` sets the class. `synonyms/synonym` with absent/empty language or `language="English"`/`"en"` supplies aliases, case-insensitively. A direct `unii` element, if present and nonempty, must hold ten uppercase letters/digits. Nested product names, salts and their UNIIs refer to separate records. [DrugBank releases](https://go.drugbank.com/releases)

### `drugsfda` and `orange_book` — FDA drug products

Set `path` to the extracted product file. Both formats expect the native header and unquoted fields. The optional `encoding` defaults to `utf-8-sig`, accepting ordinary UTF-8 and UTF-8 with BOM.

| Format | File and delimiter | Required columns | Optional evidence columns consumed |
| --- | --- | --- | --- |
| `drugsfda` | Drugs@FDA `Products.txt`, tab | `ApplNo`, `ProductNo`, `DrugName` | `ActiveIngredient`, `Strength`, `Form`. If supplied, `Appl_Type`, `Applicant_Full_Name` or `Applicant` also carry through. |
| `orange_book` | Orange Book `products.txt`, tilde (`~`) | `Appl_No`, `Product_No`, `Trade_Name` | `Appl_Type`, `Ingredient`, `Strength`, `DF;Route`, `Applicant_Full_Name` or `Applicant` |

Both readers use the native digit strings for application and product number and join them with a hyphen, for example `000123-002`. Each distinct application/product pair supplies a concept and a shared FDA cross-reference. Repeated keys may supply additional names and evidence. Blank or `NULL` product names are skipped. Drugs@FDA `Form` is preserved verbatim. Orange Book `DF;Route` splits into form and route when it contains one semicolon. The ingredient text is retained on the product evidence row. [Drugs@FDA data files](https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-data-files) · [Orange Book data files](https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files)

### `article57` — EMA Article 57 public product list

Set `path` to `.xlsx`, `.csv` or `.tsv`. For Excel use the `excel` install extra. Optional `sheet` defaults to `Art57 product data`. Text exports use optional `encoding` (default `utf-8-sig`). The reader scans past introductory rows to the row with the `Product name` heading. Captions match case-insensitively using the first line of each cell.

Required headings are `Product name`, `Active substance`, `Route of administration`, `Product authorisation country` and `Marketing authorisation holder`. An export may additionally supply `Pharmaceutical form` and `Strength`. Rows without a product name are skipped. The public file lacks a presentation identifier. With the five required values present, the output creates a `local:` SHA-256 code from their text and any form and strength. A correction to those attributes can change the local code across releases. To use a national identifier from another export, set `code_column` to its heading. A nonempty value there gives `national:<country>:<code>` with URL-escaped components. A blank identifier falls back to the local code. Product labels and reported attributes enter the dictionary and sidecar. [EMA Article 57 public product list](https://www.ema.europa.eu/en/human-regulatory-overview/post-authorisation/data-medicines-iso-idmp-standards-post-authorisation/public-data-article-57-database)

### `tables` — previously prepared dictionary

Set `path` to a directory containing `vocabularies.yaml` and one `concepts.csv`, `concepts.tsv` or `concepts.parquet`. Optionally include `synonyms`, `relationships`, `xrefs` and `product_evidence`, each with one of those three extensions. The reader accepts one file per table and only the columns listed below. Supply nonblank required columns and use string identifiers in Parquet. CSV and TSV have headers and use standard quote escaping. Existing declarations and their source aliases are merged. A vocabulary declared twice, or two different canonical spellings for the same source alias, are errors.

## Output tables

CSV files use UTF-8, comma separators, a header, and standard CSV quoting. All columns are text, including dates, identifiers, strength, application numbers and product numbers. Read them as strings to preserve leading zeroes. Optional fields with no value are empty in CSV and ordinarily `NULL` in the database. A field marked **required** must be present and nonblank on every row.

### `concepts.csv`

One row per `(vocabulary_id, concept_code)`. Names can repeat. When joining any concept reference, use both parts of the key.

| Column | Meaning |
| --- | --- |
| `vocabulary_id` **required** | Identifier namespace declared in `vocabularies.yaml`, such as `NCIt` or `RxNorm Extension`. |
| `concept_code` **required** | Code within that namespace, such as `C99900001` or `000123-002`. |
| `concept_name` **required** | Preferred display and lookup name selected by the reader. |
| `concept_class` | Source classification, such as an RxNorm term type, NCIt semantic type list, or `FDA drug product`. |
| `status` | Accepted lifecycle values are `active`, `not_current`, `remapped`, `quantified`, `obsolete`, `never_active`, and `unknown`. Blank means no value supplied. |
| `valid_start_date`, `valid_end_date` | Start and end of source validity as `YYYY-MM-DD` when supplied. |
| `replaced_by` | Replacement `concept_code` within the same `vocabulary_id`, when supplied. |
| `name_source` | Authority asserting the preferred name. Raw source spelling is retained here. For lookups an empty value falls back to `vocabulary_id`. |

### `synonyms.csv`

One row per supplied alias or eligible source atom. A source may also include the preferred spelling as a typed atom. Several authorities can assert the same string.

| Column | Meaning |
| --- | --- |
| `vocabulary_id`, `concept_code` **required** | Foreign key to `concepts`. |
| `synonym` **required** | Original alias text, usable for exact-name lookup. |
| `synonym_type` | Source term category, for example UMLS `TTY=SY`, a MeSH lexical tag, or `product name`. |
| `source` | Authority asserting this name. Raw SAB is preserved for UMLS. An empty value falls back to `vocabulary_id` in the name index. |

### `relationships.csv`

| Column | Meaning |
| --- | --- |
| `vocabulary_id_1`, `concept_code_1` **required** | Foreign key to the originating concept. |
| `predicate` **required** | Directed relationship, such as `has_ingredient`. |
| `vocabulary_id_2`, `concept_code_2` **required** | Foreign key to the target concept. |

For `has_ingredient`, endpoint 1 is the product or other containing concept and endpoint 2 is the ingredient. Treat an edge as evidence of that specific relationship. Preserve the direction and the predicate when joining.

### `xrefs.csv`

| Column | Meaning |
| --- | --- |
| `vocabulary_id`, `concept_code` **required** | Foreign key to the concept carrying the cross-reference. |
| `key_type` **required** | Namespace for the referenced identifier, such as `NCIt`, `UNII`, `UMLS_CUI`, `FDA_APPLICATION_PRODUCT`, `OMOP_CONCEPT_ID` or `OMOP_STANDARD`. |
| `key_value` **required** | Identifier within that namespace, with native formatting. An external target can be outside this build. |
| `assertion_source` | Authority that supplied the link, for example `UMLS`, `MeSH`, `FDA` or `OMOP`. This can differ from the authority that supplied the name. |

To resolve a native identifier in another included vocabulary, join `xrefs.(key_type, key_value)` to `concepts.(vocabulary_id, concept_code)` when the key namespace is that vocabulary. For example, a DrugBank record can point to `key_type=UNII` and the FDA substance's native code. A concept's own native ID is already in `concepts`. For shared external keys, compare the pair `(key_type, key_value)` across records and retain the key type in the result. `FDA_APPLICATION_PRODUCT` identifies an FDA application/product pair. `OMOP_CONCEPT_ID` identifies an Athena concept. `OMOP_STANDARD` records standard status or a `Maps to` target, which can aggregate several distinct source concepts. Grouping by a shared spelling or an OMOP mapping alone can merge distinct identities.

The following example rows come from the demo. They show how the same name reaches the native NCIt record directly and through a UMLS atom.

```csv
vocabulary_id,concept_code,synonym,synonym_type,source
NCIt,C99900001,Example drug,synonym,NCIt
UMLS,C9900001,Example drug,SY,NCI
```

```csv
vocabulary_id,concept_code,key_type,key_value,assertion_source
UMLS,C9900001,NCIt,C99900001,UMLS
```

### `product_evidence.csv` (optional)

A row records product facts as stated together in a source row. Multiple rows may join to the same product concept. Missing values mean the particular release row did not supply that field.

| Column | Meaning |
| --- | --- |
| `vocabulary_id`, `concept_code` **required** | Foreign key to the FDA, EMA or imported product concept. |
| `product_name` **required** | Drug or trade name on the source row. |
| `application_type` | Reported FDA application type, for example the Orange Book value `N` or `A`. |
| `application_number` | FDA application number, including any leading zeroes. One application can cover multiple products. |
| `product_number` | FDA product number within the application. Different strengths may have different numbers. |
| `ingredient_text` | Active ingredient or substance wording as reported. It may contain several ingredients. |
| `strength` | Reported strength value and units as supplied. |
| `dose_form` | Source dosage/pharmaceutical form text. Drugs@FDA may combine form and route here. |
| `route` | Reported route when available as a distinct field. |
| `country` | EMA product authorisation country as published. |
| `holder` | Applicant or marketing authorisation holder. |

For a FAERS row that reports only an application number, a query can return several candidate products. Use the reported name and available product number, strength, form and route to inspect the distinctions. For example, an Orange Book release containing two strengths could return `000123-001` at `5MG` and `000123-002` at `10MG` under the same trade name. On a database built with regulator inputs, inspect the original rows with a parameterized query.

```sql
SELECT c.concept_name, e.product_number, e.ingredient_text, e.strength,
       e.dose_form, e.route, e.holder
FROM product_evidence AS e
JOIN concepts AS c USING (vocabulary_id, concept_code)
WHERE e.vocabulary_id = ? AND e.application_number = ?
ORDER BY e.product_number, c.concept_name;
```

### `vocabularies.yaml`

`vocabularies` is a list with one mapping per vocabulary. Each entry has string fields `vocabulary_id`, `release` and `license` (default `unspecified`). A declaration may also include `description`. `source_aliases` maps raw authority strings to canonical ones. Keep this file beside the CSVs so consumers can attribute names and reproduce release selections.

```yaml
vocabularies:
  - vocabulary_id: NCIt
    release: synthetic-v1
    license: synthetic
  - vocabulary_id: UMLS
    release: synthetic-v1
    license: synthetic
source_aliases:
  NCI: NCIt
```

### `bundle/` (with `--build`)

`lexicon.duckdb` contains the prepared tables under their CSV base names, including `product_evidence` if supplied. Their columns are `VARCHAR`. A further indexed table, `exact_names`, contains distinct name assertions. All six of its columns are `VARCHAR`.

| Column | Meaning |
| --- | --- |
| `normalized_name` | Lookup key from Python `" ".join(text.split()).casefold()`. |
| `vocabulary_id`, `concept_code` | Foreign key to the concept. |
| `matched_name` | Original display or alias spelling for this hit. |
| `name_type` | `preferred` for a concept name, otherwise its `synonym_type`, defaulting to `synonym` when blank. |
| `source` | The original naming authority with fallback to the concept vocabulary and spelling resolved by `source_aliases`. |

Read the database using DuckDB from any application. For example, `SELECT * FROM exact_names WHERE normalized_name = 'example drug'` returns both demo hits. A query can return multiple assertions for one code. Join `concepts` to access status or validity, and apply any lifecycle or vocabulary filter explicitly. Building the index preserves ambiguity. The [Python example](README.md#look-up-a-reported-name) binds the normalized search string and orders the hits. For a CSV-only consumer, Python's standard library preserves the source text and code fields directly.

```python
import csv
from pathlib import Path

with Path("data/demo/concepts.csv").open(encoding="utf-8", newline="") as f:
    labels = {(r["vocabulary_id"], r["concept_code"]): r["concept_name"] for r in csv.DictReader(f)}
print(labels[("NCIt", "C99900001")])  # Example compound
```

`manifest.json` records `format` (`mprint-exact-names`), integer `format_version` (`1`), `database` (relative filename), `sha256` (digest of the closed database file), `normalization` (text), `counts` (table name to integer row count), and the `vocabularies` and `source_aliases` from the declaration. Verify the stored digest against the database bytes when moving a bundle. The demo has 3 concepts, 4 synonym rows, 2 cross-references, 0 relationships and 7 indexed name assertions.
