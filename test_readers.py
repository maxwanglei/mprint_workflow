"""Native release readers handle the shapes real and exported releases take."""

import io
import zipfile

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


DRUG = (
    '<drug type="small molecule"><drugbank-id primary="true">DB{n:05d}</drugbank-id>'
    "<name>D{n}</name><unii>{unii}</unii></drug>"
)


def drugbank_xml(*uniis):
    body = "".join(DRUG.format(n=i, unii=u) for i, u in enumerate(uniis, 1))
    return f'<drugbank xmlns="http://www.drugbank.ca">{body}</drugbank>'


def zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_drugbank_zip_ignores_macos_metadata(run_source, read_rows):
    data = zip_bytes(
        {
            "full database.xml": drugbank_xml("ABCDEFGHIJ"),
            "__MACOSX/._full database.xml": b"\x00\x05\x16\x07",
        }
    )
    out = run_source({"format": "drugbank", **NATIVE, "path": "db.zip"}, {"db.zip": data})
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["DB00001"]


def test_invalid_unii_drops_only_that_link(run_source, read_rows, capsys):
    out = run_source(
        {"format": "drugbank", **NATIVE, "path": "db.xml"},
        {"db.xml": drugbank_xml("ABCDEFGHIJ", "abcdefghij")},
    )
    assert len(read_rows(out, "concepts")) == 2
    assert [r["key_value"] for r in read_rows(out, "xrefs")] == ["ABCDEFGHIJ"]
    assert "skipped 1 invalid UNII" in capsys.readouterr().err


def test_gsrs_zip_with_readme_member(run_source, read_rows):
    names = "NAME\tTYPE\tUNII\tDISPLAY_NAME\n" + "Aspirin\tcn\tR16CO5Y76E\tASPIRIN\n" * 2
    data = zip_bytes({"UNII_Names_4Aug2026.txt": names, "READ ME UNII Lists.txt": "readme"})
    out = run_source({"format": "gsrs", **NATIVE, "path": "UNIIs.zip"}, {"UNIIs.zip": data})
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["R16CO5Y76E"]
    assert [r["synonym"] for r in read_rows(out, "synonyms")] == ["Aspirin"]
