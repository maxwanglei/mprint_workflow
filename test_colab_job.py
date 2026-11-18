"""The Colab job stages a Drive project folder on local disk, builds, and publishes back."""

import csv
import runpy
import shutil
from pathlib import Path

import pytest

JOB = Path(__file__).parent / "colab" / "colab_job.py"
DEMO = Path(__file__).parent / "demo"


@pytest.fixture
def drive(tmp_path):
    root = tmp_path / "drive" / "mprint"
    shutil.copytree(DEMO, root)
    return root


@pytest.fixture
def run_job(monkeypatch, tmp_path):
    def run(drive, **settings):
        monkeypatch.setenv("MPRINT_DRIVE_ROOT", str(drive))
        monkeypatch.setenv("MPRINT_WORK", str(tmp_path / "work"))
        monkeypatch.setenv("MPRINT_RUN", "r1")
        for name, value in settings.items():
            monkeypatch.setenv(name, value)
        runpy.run_path(str(JOB), run_name="__main__")

    return run


def vocabularies(table: Path) -> set[str]:
    with table.open(encoding="utf-8", newline="") as stream:
        return {row["vocabulary_id"] for row in csv.DictReader(stream)}


def test_job_publishes_the_build_to_drive(run_job, drive, tmp_path):
    run_job(drive)
    published = drive / "outputs" / "r1"
    assert (published / "bundle" / "manifest.json").is_file()
    local = tmp_path / "work" / "outputs" / "r1" / "concepts.csv"
    assert (published / "concepts.csv").read_bytes() == local.read_bytes()


def test_job_builds_only_the_named_sources(run_job, drive):
    run_job(drive, MPRINT_ONLY="ncit", MPRINT_BUILD="0")
    published = drive / "outputs" / "r1"
    assert vocabularies(published / "concepts.csv") == {"NCIt"}
    assert not (published / "bundle").exists()


def test_job_does_not_stage_earlier_outputs(run_job, drive, tmp_path):
    (drive / "outputs" / "old").mkdir(parents=True)
    (drive / "outputs" / "old" / "concepts.csv").write_text("stale")
    run_job(drive)
    assert not (tmp_path / "work" / "input" / "outputs").exists()


def test_job_refuses_an_existing_run_before_building(run_job, drive, tmp_path):
    (drive / "outputs" / "r1").mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        run_job(drive)
    assert not (tmp_path / "work" / "outputs").exists()


def test_job_explains_an_unmounted_drive(run_job, tmp_path):
    with pytest.raises(ValueError, match="colab drivemount"):
        run_job(tmp_path / "drive" / "missing")


def test_job_stages_only_the_selected_sources_files(run_job, drive, tmp_path):
    (drive / "raw" / "umls").mkdir(parents=True)
    (drive / "raw" / "umls" / "unrelated.bin").write_bytes(b"x" * 1000)
    run_job(drive, MPRINT_ONLY="ncit", MPRINT_BUILD="0")
    staged = {p.name for p in (tmp_path / "work" / "input").rglob("*") if p.is_file()}
    assert staged == {"sources.yaml", "Thesaurus.txt"}
