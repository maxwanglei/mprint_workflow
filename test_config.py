"""Source configuration is rejected with a named option before any release file is read."""

import pytest

NCIT = {"format": "ncit", "vocabulary_id": "V", "release": "r", "path": "t.txt"}
ROW = "C1\tiri\t\tDrug A\t\t\t\tPharmacologic Substance\n"


@pytest.mark.parametrize("value", [None, "", 5])
def test_path_must_be_nonempty_text(run_source, value):
    with pytest.raises(ValueError, match="source 'src': path must be a nonempty string"):
        run_source({**NCIT, "path": value}, {"t.txt": ROW})


@pytest.mark.parametrize(
    "settings, option",
    [
        ({"format": "article57", "code_column": 123}, "code_column"),
        ({"format": "drugsfda", "encoding": 123}, "encoding"),
    ],
)
def test_scalar_options_must_be_text(run_source, settings, option):
    with pytest.raises(ValueError, match=f"{option} must be a nonempty string"):
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
