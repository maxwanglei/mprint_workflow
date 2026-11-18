"""Damaged or unexpected release files fail with a source-named error, never a traceback."""

import gzip
import io
import zipfile

import pytest

NATIVE = {"vocabulary_id": "V", "release": "r"}
NCIT_ROW = "C1\tiri\t\tDrug A\t\t\t\tPharmacologic Substance\n"
MESH_XML = (
    b"<DescriptorRecordSet>"
    + b"".join(
        f"<DescriptorRecord><DescriptorUI>D{i}</DescriptorUI>"
        f"<DescriptorName><String>N{i}</String></DescriptorName>"
        "<ConceptList><Concept PreferredConceptYN='Y'/></ConceptList>"
        "</DescriptorRecord>".encode()
        for i in range(4000)
    )
    + b"</DescriptorRecordSet>"
)
MRCONSO_ROW = "C1|ENG|P|L|PF|S|Y|A||||NCI|PT|C1|Drug A||N||\n"


def truncated_gzip(payload: bytes) -> bytes:
    data = gzip.compress(payload)
    return data[: len(data) // 2]


def test_truncated_mesh_gzip(run_source):
    with pytest.raises(ValueError, match="source 'src'.*Compressed file ended"):
        run_source(
            {"format": "mesh", **NATIVE, "descriptors": "d.xml.gz"},
            {"d.xml.gz": truncated_gzip(MESH_XML)},
        )


def test_truncated_umls_gzip(run_source):
    with pytest.raises(ValueError, match="source 'src'.*Compressed file ended"):
        run_source(
            {"format": "umls", **NATIVE, "mrconso": "m.rrf.gz", "sources": ["NCI"]},
            {"m.rrf.gz": truncated_gzip((MRCONSO_ROW * 5000).encode())},
        )


def test_corrupt_gsrs_deflate(run_source):
    member = "UNII_Names.txt"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            member, "NAME\tTYPE\tUNII\tDISPLAY_NAME\n" + "B\tof\tABCDEFGHIJ\tA\n" * 500
        )
    data = bytearray(buffer.getvalue())
    data[30 + len(member)] = 0xFF
    with pytest.raises(ValueError, match="source 'src'.*invalid block type"):
        run_source({"format": "gsrs", **NATIVE, "path": "u.zip"}, {"u.zip": bytes(data)})


def test_xlsx_that_is_not_a_workbook(run_source):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a workbook")
    with pytest.raises(ValueError, match="not an Excel workbook") as info:
        run_source(
            {"format": "article57", **NATIVE, "path": "a.xlsx"}, {"a.xlsx": buffer.getvalue()}
        )
    assert "missing config key" not in str(info.value)


def test_unselected_ncit_row_over_128k(run_source, read_rows):
    gene = "C2\tiri\t\tGene A\t" + "x" * 140_000 + "\t\t\tGene\n"
    out = run_source(
        {
            "format": "ncit",
            **NATIVE,
            "path": "t.txt",
            "semantic_types": ["Pharmacologic Substance"],
        },
        {"t.txt": NCIT_ROW + gene},
    )
    assert [r["concept_code"] for r in read_rows(out, "concepts")] == ["C1"]
