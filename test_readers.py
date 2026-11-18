"""Native release readers handle the shapes real and exported releases take."""

import gzip
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


DESCRIPTORS = """<DescriptorRecordSet>
<DescriptorRecord><DescriptorUI>D000001</DescriptorUI>
<DescriptorName><String>Label</String></DescriptorName>
<TreeNumberList><TreeNumber>D01.123</TreeNumber></TreeNumberList>
<ConceptList>
<Concept PreferredConceptYN="Y"><RegistryNumber>ABCDEFGHIJ</RegistryNumber><TermList>
<Term><String>Label</String></Term>
<Term LexicalTag="NON"><String>Alias</String></Term>
<Term IsPermutedTermYN="Y"><String>Permuted</String></Term>
</TermList></Concept>
<Concept PreferredConceptYN="N"><TermList><Term><String>Narrower</String></Term></TermList></Concept>
</ConceptList></DescriptorRecord>
<DescriptorRecord><DescriptorUI>D000002</DescriptorUI>
<DescriptorName><String>Disease</String></DescriptorName>
<TreeNumberList><TreeNumber>C01</TreeNumber></TreeNumberList>
<ConceptList><Concept PreferredConceptYN="Y"/></ConceptList></DescriptorRecord>
</DescriptorRecordSet>"""
SUPPLEMENTAL = """<SupplementalRecordSet>
<SupplementalRecord SCRClass="2"><SupplementalRecordUI>C000001</SupplementalRecordUI>
<SupplementalRecordName><String>Protocol</String></SupplementalRecordName>
<ConceptList><Concept PreferredConceptYN="Y"/></ConceptList></SupplementalRecord>
</SupplementalRecordSet>"""
MESH = {"format": "mesh", **NATIVE, "descriptors": "d.xml"}


def test_mesh_file_with_no_matching_records(run_source):
    with pytest.raises(ValueError, match="no SupplementalRecord records match"):
        run_source(
            {**MESH, "supplemental": "s.xml", "scr_classes": [1]},
            {"d.xml": DESCRIPTORS, "s.xml": SUPPLEMENTAL},
        )


def test_mesh_descriptor_aliases_and_unii(run_source, read_rows):
    out = run_source(
        {**MESH, "descriptors": "d.xml.gz", "descriptor_tree_prefixes": ["D01"]},
        {"d.xml.gz": gzip.compress(DESCRIPTORS.encode())},
    )
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["D000001"]
    assert [(r["synonym"], r["synonym_type"]) for r in read_rows(out, "synonyms")] == [
        ("Alias", "NON")
    ]
    assert [(r["key_type"], r["key_value"]) for r in read_rows(out, "xrefs")] == [
        ("UNII", "ABCDEFGHIJ")
    ]
