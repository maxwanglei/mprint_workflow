"""Native release readers handle the shapes real and exported releases take."""

import io

import pytest

NATIVE = {"vocabulary_id": "V", "release": "r"}
A57 = {"format": "article57", **NATIVE, "path": "a.csv"}
CAPTIONS = (
    "Product name,Active substance,Route of administration,"
    "Product authorisation country,Marketing authorisation holder"
)


def test_short_row_with_code_column_keeps_blank_holder(run_source, read_rows):
    header = (
        "Product name,Local code,Active substance,Route of administration,"
        "Product authorisation country,Marketing authorisation holder\n"
    )
    out = run_source(
        {**A57, "code_column": "Local code"}, {"a.csv": header + "Drug A,123,sub,oral,France\n"}
    )
    [row] = read_rows(out, "product_evidence")
    assert (row["concept_code"], row["holder"]) == ("national:France:123", "")


def test_header_found_after_legend_row(run_source, read_rows):
    text = "Legend,Product name\n" + CAPTIONS + "\nDrug A,sub,oral,France,Firm\n"
    out = run_source(A57, {"a.csv": text})
    assert [r["concept_name"] for r in read_rows(out, "concepts")] == ["Drug A"]


def test_duplicate_caption_rejected(run_source):
    text = CAPTIONS + ",Strength,Strength\nDrug A,sub,oral,France,Firm,5 mg,10 mg\n"
    with pytest.raises(ValueError, match="duplicate Article 57 column"):
        run_source(A57, {"a.csv": text})


def test_xlsx_with_title_rows(run_source, read_rows):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Art57 product data"
    sheet.append(["Article 57 product data"])
    sheet.append([])
    sheet.append([f"{caption}\nDefinition text" for caption in CAPTIONS.split(",")])
    sheet.append(["Drug A", "sub", "oral", "France", "Firm"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    out = run_source({**A57, "path": "a.xlsx"}, {"a.xlsx": buffer.getvalue()})
    assert [r["concept_name"] for r in read_rows(out, "concepts")] == ["Drug A"]
