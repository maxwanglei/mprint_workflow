"""Shared fixtures that run one configured source through the public prepare() entry."""

import csv
from pathlib import Path

import pytest
import yaml

from mprint_workflow.prepare_dictionaries import prepare


@pytest.fixture
def run_source(tmp_path):
    def run(settings, files=None, *, build=False, extra=None):
        for relative, content in (files or {}).items():
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        config = tmp_path / "sources.yaml"
        config.write_text(
            yaml.safe_dump({"sources": {"src": settings, **(extra or {})}}, sort_keys=False)
        )
        output = tmp_path / "out"
        prepare(config, output, [], build)
        return output

    return run


@pytest.fixture
def read_rows():
    def read(output: Path, table: str) -> list[dict[str, str]]:
        with (output / f"{table}.csv").open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    return read
