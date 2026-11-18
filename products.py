"""Index the reported product names in FDA and EMA regulatory releases.

FDA product identity is the pair (application number, product number). Each
strength can have its own product number and share a trade name with others.
The public Article 57 sheet has no medicinal-product ID or strength; its
local keys identify distinct published name/substance/route/country/holder
combinations. Neither an ingredient name nor an application number alone
asserts product equivalence.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _fda_products(config: Mapping[str, Any], sink: Any, *, orange_book: bool) -> None:
    path = sink.path(config["path"])
    vocabulary = config["vocabulary_id"]
    if orange_book:
        columns = ("Appl_No", "Product_No", "Trade_Name")
        delimiter = "~"
    else:
        columns = ("ApplNo", "ProductNo", "DrugName")
        delimiter = "\t"

    # Multipart/therapeutic-equivalence records can repeat the product
    # key. Preserve distinct source-asserted names while emitting one concept.
    names_by_code: dict[str, set[str]] = {}
    with path.open(encoding=config.get("encoding", "utf-8-sig"), newline="") as stream:
        reader = csv.DictReader(stream, delimiter=delimiter, quoting=csv.QUOTE_NONE)
        missing = set(columns) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing FDA product column(s) {sorted(missing)}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path}:{reader.line_num}: unexpected number of FDA fields")
            application, product, name = (row[column].strip() for column in columns)
            if not name or name.upper() == "NULL":
                continue  # DrugName is nullable and this row supplies no name.
            if not (
                application.isascii()
                and application.isdigit()
                and product.isascii()
                and product.isdigit()
            ):
                raise ValueError(f"{path}:{reader.line_num}: invalid FDA application/product key")
            code = f"{application}-{product}"
            # FDA defines the Orange Book field as dosage form;route. Keep
            # the Drugs@FDA Form field intact, including multipart entries.
            form = row.get("DF;Route" if orange_book else "Form", "")
            route = ""
            if orange_book and form.count(";") == 1:
                form, route = form.split(";")
            sink.add(
                "product_evidence",
                vocabulary_id=vocabulary,
                concept_code=code,
                product_name=name,
                application_type=row.get("Appl_Type", ""),
                application_number=application,
                product_number=product,
                ingredient_text=row.get("Ingredient" if orange_book else "ActiveIngredient", ""),
                strength=row.get("Strength", ""),
                dose_form=form,
                route=route,
                holder=row.get("Applicant_Full_Name") or row.get("Applicant", ""),
            )
            names = names_by_code.setdefault(code, set())
            if name in names:
                continue
            if not names:
                sink.add(
                    "concepts",
                    vocabulary_id=vocabulary,
                    concept_code=code,
                    concept_name=name,
                    concept_class="FDA drug product",
                    name_source="FDA",
                    status="unknown",
                )
                sink.add(
                    "xrefs",
                    vocabulary_id=vocabulary,
                    concept_code=code,
                    key_type="FDA_APPLICATION_PRODUCT",
                    key_value=code,
                    assertion_source="FDA",
                )
            else:
                sink.add(
                    "synonyms",
                    vocabulary_id=vocabulary,
                    concept_code=code,
                    synonym=name,
                    synonym_type="product name",
                    source="FDA",
                )
            names.add(name)


def read_drugsfda(config: Mapping[str, Any], sink: Any) -> None:
    """Read the tab-separated Drugs@FDA Products.txt table."""
    _fda_products(config, sink, orange_book=False)


def read_orange_book(config: Mapping[str, Any], sink: Any) -> None:
    """Read the tilde-delimited FDA Orange Book products.txt table."""
    _fda_products(config, sink, orange_book=True)


def _article_rows(path: Path, config: Mapping[str, Any]) -> Iterator[Sequence[Any]]:
    if path.suffix.casefold() == ".xlsx":
        try:
            from openpyxl import load_workbook  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ValueError(
                "Article 57 XLSX needs openpyxl; install openpyxl or export to CSV"
            ) from exc
        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
        except KeyError as exc:
            raise ValueError(f"{path}: not an Excel workbook ({exc})") from exc
        try:
            sheet = config.get("sheet", "Art57 product data")
            if sheet not in workbook.sheetnames:
                raise ValueError(
                    f"{path}: worksheet {sheet!r} absent; specify 'sheet' in the source config"
                )
            yield from workbook[sheet].iter_rows(values_only=True)
        finally:
            workbook.close()
    elif path.suffix.casefold() in {".csv", ".tsv"}:
        with path.open(encoding=config.get("encoding", "utf-8-sig"), newline="") as stream:
            yield from csv.reader(
                stream, delimiter="\t" if path.suffix.casefold() == ".tsv" else ","
            )
    else:
        raise ValueError(
            f"{path}: expected an Article 57 XLSX workbook or CSV/TSV worksheet export"
        )


def _article_header(cells: Iterable[Any]) -> list[str]:
    # The workbook puts a multi-line definition below the caption in the
    # same header cell. CSV exports retain these embedded line breaks.
    return [str(cell or "").strip().split("\n", 1)[0].strip().casefold() for cell in cells]


def read_article57(config: Mapping[str, Any], sink: Any) -> None:
    """Read EMA Article 57 product names, excluding pharmacovigilance contacts.

    A client export may supply a source-assigned code_column. The resulting
    codes are country-scoped. Otherwise use a deterministic SHA-256 of the
    public product attributes (plus form and strength if supplied in an export).
    """
    path = sink.path(config["path"])
    vocabulary = config["vocabulary_id"]
    required = [
        "product name",
        "active substance",
        "route of administration",
        "product authorisation country",
        "marketing authorisation holder",
    ]
    code_column = config.get("code_column")
    header: list[str] = []
    names_by_code: dict[str, set[str]] = {}
    for number, cells in enumerate(_article_rows(path, config), 1):
        if not header:
            candidate = _article_header(cells)
            if "product name" not in candidate:
                continue
            required_columns = required + ([code_column.casefold()] if code_column else [])
            missing = set(required_columns) - set(candidate)
            if missing:
                raise ValueError(f"{path}:{number}: missing Article 57 column(s) {sorted(missing)}")
            header = candidate
            continue
        row = {
            key: "" if value is None else str(value).strip()
            for key, value in zip(header, cells, strict=False)
        }
        name = row.get("product name", "")
        if not name:
            continue  # Some published rows have no searchable product name.
        attributes = [row.get(key, "") for key in required]
        country = row.get("product authorisation country", "")
        if not country:
            raise ValueError(f"{path}:{number}: missing product authorisation country")
        native = row.get(code_column.casefold(), "") if code_column else ""
        if native:
            code = f"national:{quote(country, safe='')}:{quote(native, safe='')}"
        else:
            if not all(attributes):
                raise ValueError(
                    f"{path}:{number}: incomplete attributes for a local Article 57 name key"
                )
            attributes.extend(row.get(key, "") for key in ("pharmaceutical form", "strength"))
            serialized = json.dumps(attributes, ensure_ascii=False, separators=(",", ":"))
            code = "local:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        sink.add(
            "product_evidence",
            vocabulary_id=vocabulary,
            concept_code=code,
            product_name=name,
            ingredient_text=row["active substance"],
            strength=row.get("strength", ""),
            dose_form=row.get("pharmaceutical form", ""),
            route=row["route of administration"],
            country=country,
            holder=row["marketing authorisation holder"],
        )
        names = names_by_code.setdefault(code, set())
        if name in names:
            continue
        if names:
            sink.add(
                "synonyms",
                vocabulary_id=vocabulary,
                concept_code=code,
                synonym=name,
                synonym_type="product name",
                source="EMA",
            )
        else:
            sink.add(
                "concepts",
                vocabulary_id=vocabulary,
                concept_code=code,
                concept_name=name,
                concept_class="Article 57 product listing",
                name_source="EMA",
                status="active",
            )
        names.add(name)
    if not header:
        raise ValueError(f"{path}: Article 57 product header not found")
