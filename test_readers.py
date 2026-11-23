"""Native release readers handle the shapes real and exported releases take."""

import gzip
import io
import zipfile
from pathlib import Path

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


def test_drugbank_external_identifiers_and_salts(run_source, read_rows, capsys):
    xml = (
        '<drugbank xmlns="http://www.drugbank.ca"><drug type="small molecule">'
        '<drugbank-id primary="true">DB00001</drugbank-id><name>Drug A</name><unii>ABCDEFGHIJ</unii>'
        '<salts><salt><drugbank-id primary="true">DBSALT000001</drugbank-id>'
        "<name>Drug A sodium</name><unii>KLMNOPQRST</unii></salt></salts>"
        "<external-identifiers>"
        "<external-identifier><resource>RxCUI</resource><identifier>1191</identifier></external-identifier>"
        "<external-identifier><resource>RxCUI</resource><identifier>not-a-code</identifier></external-identifier>"
        "<external-identifier><resource>ChEBI</resource><identifier>15365</identifier></external-identifier>"
        "<external-identifier><resource>Wikipedia</resource><identifier>Drug_A</identifier></external-identifier>"
        "</external-identifiers></drug></drugbank>"
    )
    out = run_source({"format": "drugbank", **NATIVE, "path": "db.xml"}, {"db.xml": xml})
    concepts = {
        (r["concept_code"], r["concept_name"], r["concept_class"])
        for r in read_rows(out, "concepts")
    }
    assert concepts == {
        ("DB00001", "Drug A", "small molecule"),
        ("DBSALT000001", "Drug A sodium", "salt"),
    }
    assert keys(out, read_rows) == [
        ("DB00001", "ChEBI", "15365"),
        ("DB00001", "RxNorm", "1191"),
        ("DB00001", "UNII", "ABCDEFGHIJ"),
        ("DBSALT000001", "UNII", "KLMNOPQRST"),
    ]
    [edge] = read_rows(out, "relationships")
    assert (edge["concept_code_1"], edge["predicate"], edge["concept_code_2"]) == (
        "DBSALT000001",
        "salt_of",
        "DB00001",
    )
    assert "skipped 1 invalid UNII or identifier" in capsys.readouterr().err


def test_drugbank_salts_can_be_left_out(run_source, read_rows):
    xml = (
        '<drugbank xmlns="http://www.drugbank.ca"><drug type="biotech">'
        '<drugbank-id primary="true">DB00002</drugbank-id><name>Drug B</name>'
        "<salts><salt><drugbank-id>DBSALT000002</drugbank-id><name>Drug B acetate</name></salt></salts>"
        "</drug></drugbank>"
    )
    out = run_source(
        {"format": "drugbank", **NATIVE, "path": "db.xml", "salts": False}, {"db.xml": xml}
    )
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["DB00002"]
    assert read_rows(out, "relationships") == []


def test_gsrs_records_add_rxnorm_ncit_and_pubchem_keys(run_source, read_rows):
    names = "NAME\tTYPE\tUNII\tDISPLAY_NAME\nAspirin\tcn\tR16CO5Y76E\tASPIRIN\n"
    records = (
        "UNII\tDISPLAY_NAME\tRN\tEC\tNCIT\tRXCUI\tPUBCHEM\n"
        "R16CO5Y76E\tASPIRIN\t50-78-2\t\tC287\t1191\t2244\n"
        "ZZZZZZZZZZ\tNOT IN NAMES\t\t\tC1\t1\t1\n"
        "R16CO5Y76E\tASPIRIN\t\t\tbad\t\t\n"
    )
    out = run_source(
        {"format": "gsrs", **NATIVE, "path": "UNIIs.zip", "records": "UNII_Data.zip"},
        {
            "UNIIs.zip": zip_bytes({"UNII_Names_4Aug2026.txt": names}),
            "UNII_Data.zip": zip_bytes(
                {"UNII_Records_4Aug2026.txt": records, "Legacy UNIIs.txt": ""}
            ),
        },
    )
    assert keys(out, read_rows) == [
        ("R16CO5Y76E", "NCIt", "C287"),
        ("R16CO5Y76E", "PubChem CID", "2244"),
        ("R16CO5Y76E", "RxNorm", "1191"),
    ]
    assert {r["assertion_source"] for r in read_rows(out, "xrefs")} == {"FDA UNII Records"}


def test_ncit_parents_become_is_a_edges_within_the_selection(run_source, read_rows):
    thesaurus = (
        "C1\tiri\tC2|C3\tDrug A\t\t\t\tPharmacologic Substance\n"
        "C2\tiri\tC4\tDrug class\t\t\t\tPharmacologic Substance\n"
        "C3\tiri\t\tA gene\t\t\t\tGene or Genome\n"
    )
    out = run_source(
        {
            "format": "ncit",
            **NATIVE,
            "path": "t.txt",
            "semantic_types": ["Pharmacologic Substance"],
        },
        {"t.txt": thesaurus},
    )
    edges = [
        (r["concept_code_1"], r["predicate"], r["concept_code_2"])
        for r in read_rows(out, "relationships")
    ]
    assert edges == [("C1", "is_a", "C2")]


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


UMLS = {"format": "umls", **NATIVE, "mrconso": "m.rrf", "sources": ["NCI"]}
SPLIT_CODE = (
    "C1|ENG|P|L|PF|S1|Y|A1||||NCI|PT|C9|Drug A||N||\n"
    "C2|ENG|P|L|PF|S2|Y|A2||||NCI|SY|C9|Drug A gene||N||\n"
)
SEMANTIC_TYPES = "C1|T121|A|Pharmacologic Substance|AT1||\nC2|T028|A|Gene|AT2||\n"


def test_umls_spills_under_run_scratch(run_source, monkeypatch, tmp_path):
    import mprint_workflow.umls as umls

    seen = []
    original = umls.TemporaryDirectory

    def spy(*args, **kwargs):
        seen.append(kwargs.get("dir"))
        return original(*args, **kwargs)

    monkeypatch.setattr(umls, "TemporaryDirectory", spy)
    run_source(UMLS, {"m.rrf": SPLIT_CODE})
    assert seen[0] is not None and Path(seen[0]).is_relative_to(tmp_path)


def keys(out, read_rows):
    return sorted(
        (r["concept_code"], r["key_type"], r["key_value"]) for r in read_rows(out, "xrefs")
    )


def test_code_split_outside_semantic_filter_is_not_one_to_one(run_source, read_rows):
    out = run_source(
        {**UMLS, "mrsty": "s.rrf", "semantic_types": ["T121"], "code_identity": "any"},
        {"m.rrf": SPLIT_CODE, "s.rrf": SEMANTIC_TYPES},
    )
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["C1"]
    assert [r["key_type"] for r in read_rows(out, "xrefs")] == ["UMLS_CUI"]


def test_code_split_outside_tty_filter_is_not_one_to_one(run_source, read_rows):
    out = run_source({**UMLS, "ttys": ["PT"], "code_identity": "any"}, {"m.rrf": SPLIT_CODE})
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["C1"]
    assert [r["key_type"] for r in read_rows(out, "xrefs")] == ["UMLS_CUI"]


def test_code_follows_its_preferred_term_across_a_split(run_source, read_rows):
    out = run_source(
        {**UMLS, "mrsty": "s.rrf", "semantic_types": ["T121"]},
        {"m.rrf": SPLIT_CODE, "s.rrf": SEMANTIC_TYPES},
    )
    assert keys(out, read_rows) == [("C1", "NCIt", "C9"), ("C1", "UMLS_CUI", "C1")]


def test_brand_name_filed_under_a_brand_concept_keeps_the_ingredient_key(run_source, read_rows):
    # NCI files the brand name "Brandex" under its ingredient code C9;
    # UMLS puts that atom in the brand's own concept C2.
    mrconso = (
        "C1|ENG|P|L|PF|S1|Y|A1||||NCI|PT|C9|Drug A||N||\n"
        "C2|ENG|P|L|PF|S2|Y|A2||||NCI|BN|C9|Brandex||N||\n"
        "C2|ENG|S|L|PF|S3|N|A3||||RXNORM|BN|222|Brandex||N||\n"
    )
    out = run_source({**UMLS, "sources": ["NCI", "RXNORM"]}, {"m.rrf": mrconso})
    assert keys(out, read_rows) == [
        ("C1", "NCIt", "C9"),
        ("C1", "UMLS_CUI", "C1"),
        ("C2", "RxNorm", "222"),
        ("C2", "UMLS_CUI", "C2"),
    ]


def test_preferred_term_in_two_concepts_is_still_ambiguous(run_source, read_rows):
    mrconso = (
        "C1|ENG|P|L|PF|S1|Y|A1||||NCI|PT|C9|Drug A||N||\n"
        "C2|ENG|P|L|PF|S1|Y|A2||||NCI|PT|C9|Drug A||N||\n"
    )
    out = run_source(UMLS, {"m.rrf": mrconso})
    assert {r["key_type"] for r in read_rows(out, "xrefs")} == {"UMLS_CUI"}


def test_unknown_code_identity_is_rejected(run_source):
    with pytest.raises(ValueError, match="code_identity"):
        run_source({**UMLS, "code_identity": "maybe"}, {"m.rrf": SPLIT_CODE})


DRUGSFDA = {"format": "drugsfda", **NATIVE, "path": "Products.txt"}
PRODUCTS = (
    "ApplNo\tProductNo\tForm\tStrength\tReferenceDrug\tDrugName\tActiveIngredient\tReferenceStandard\n"
    "000123\t001\tTABLET;ORAL\t5MG\t0\tEXAMPLE\tEXAMPLE DRUG\t0\n"
    "000456\t001\tKIT\tN/A\t0\tEXAMPLE KIT\tEXAMPLE DRUG\t0\n"
)
APPLICATIONS = (
    "ApplNo\tApplType\tApplPublicNotes\tSponsorName\n"
    "000123\tNDA\t\tEXAMPLE PHARMA\n000456\tANDA\t\tOTHER PHARMA\n"
)


def test_drugsfda_joins_applications(run_source, read_rows):
    out = run_source(
        {**DRUGSFDA, "applications": "Applications.txt"},
        {"Products.txt": PRODUCTS, "Applications.txt": APPLICATIONS},
    )
    facts = {
        r["application_number"]: (r["application_type"], r["holder"])
        for r in read_rows(out, "product_evidence")
    }
    assert facts == {"000123": ("NDA", "EXAMPLE PHARMA"), "000456": ("ANDA", "OTHER PHARMA")}


def test_drugsfda_splits_a_single_semicolon_form(run_source, read_rows):
    out = run_source(DRUGSFDA, {"Products.txt": PRODUCTS})
    forms = {(r["dose_form"], r["route"]) for r in read_rows(out, "product_evidence")}
    assert forms == {("TABLET", "ORAL"), ("KIT", "")}


def test_drugsfda_reads_cp1252(run_source, read_rows):
    text = PRODUCTS.replace("\tEXAMPLE\t", "\tEXAMPLE®\t")
    out = run_source({**DRUGSFDA, "encoding": "cp1252"}, {"Products.txt": text.encode("cp1252")})
    assert "EXAMPLE®" in {r["concept_name"] for r in read_rows(out, "concepts")}


def test_excluded_tty_atom_adds_no_native_xref(run_source, read_rows):
    mrconso = (
        "C1|ENG|P|L|PF|S1|Y|A1||||RXNORM|IN|111|Drug A||N||\n"
        "C1|ENG|S|L|PF|S2|N|A2||||MSH|MH|D000001|Drug A heading||N||\n"
    )
    out = run_source({**UMLS, "sources": ["RXNORM", "MSH"], "ttys": ["IN"]}, {"m.rrf": mrconso})
    keys = sorted((r["key_type"], r["key_value"]) for r in read_rows(out, "xrefs"))
    assert keys == [("RxNorm", "111"), ("UMLS_CUI", "C1")]


def test_blank_name_on_excluded_atom_is_ignored(run_source, read_rows):
    mrconso = (
        "C1|ENG|P|L|PF|S1|Y|A1||||RXNORM|IN|111|Drug A||N||\n"
        "C2|ENG|P|L|PF|S2|Y|A2||||RXNORM|BN|222|||N||\n"
    )
    out = run_source({**UMLS, "sources": ["RXNORM"], "ttys": ["IN"]}, {"m.rrf": mrconso})
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["C1"]
