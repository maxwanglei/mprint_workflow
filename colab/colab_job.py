"""Build the MPRINT dictionary on a Colab VM from a Google Drive project folder.

Run it from your computer with the Colab CLI, after `colab drivemount`:

    colab exec -s mprint -f colab/colab_job.py --timeout 0 --env MPRINT_ONLY="ncit gsrs"

The job installs the newest wheel from `<project>/wheels`, copies the YAML and
the selected sources' release files from Drive to the VM's local disk, and
builds there. It then
copies the finished output to `<project>/outputs/<run>` and flushes Drive.
SQLite and DuckDB scratch files never touch the Drive mount. Settings come from
the MPRINT_* environment variables listed in colab/README.md.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

NOT_STAGED = {"outputs", "wheels"}
PATH_OPTIONS = ("path", "mrconso", "mrsty", "descriptors", "supplemental", "applications")


def referenced(project: Path, config: str, only: list[str]) -> list[Path]:
    """Return the config and the relative input paths of the selected sources.

    A config the job cannot read selects the whole project folder, so prepare
    reports the problem with its own message.
    """
    import yaml

    try:
        sources = yaml.safe_load((project / config).read_text(encoding="utf-8"))["sources"]
        values = [
            settings.get(key)
            for name, settings in sources.items()
            if not only or name in only
            for key in PATH_OPTIONS
        ]
    except (yaml.YAMLError, AttributeError, KeyError, TypeError):
        return [Path(".")]
    base = Path(config).parent
    relative = [v for v in values if isinstance(v, str) and v.strip()]
    return [Path(config)] + [base / v for v in relative if not Path(v).expanduser().is_absolute()]


def sync(source: Path, target: Path, members: list[Path]) -> int:
    """Copy new or changed files under the members of source and return the bytes copied."""
    copied = 0
    for member in members:
        root = source / member
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            copied += _copy(source, target, path)
    return copied


def _copy(source: Path, target: Path, path: Path) -> int:
    relative = path.relative_to(source)
    if relative.parts[0] in NOT_STAGED or path.name.startswith(".") or not path.is_file():
        return 0
    destination = target / relative
    stat = path.stat()
    if destination.is_file():
        current = destination.stat()
        if (current.st_size, int(current.st_mtime)) == (stat.st_size, int(stat.st_mtime)):
            return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return stat.st_size


def install(project: Path) -> None:
    """Install the configured or newest project wheel, replacing any copy of the same version."""
    if os.environ.get("MPRINT_WHEEL"):
        wheels = [project / os.environ["MPRINT_WHEEL"]]
    else:
        wheels = sorted((project / "wheels").glob("*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        return
    wheel = wheels[-1]
    pip = [sys.executable, "-m", "pip", "install", "--quiet"]
    subprocess.check_call([*pip, f"{wheel}[excel]"])
    subprocess.check_call([*pip, "--force-reinstall", "--no-deps", str(wheel)])
    for name in [m for m in sys.modules if m.split(".")[0] == "mprint_workflow"]:
        del sys.modules[name]
    print(f"installed {wheel.name}", flush=True)


def flush_drive() -> None:
    try:
        from google.colab import drive
    except ImportError:
        return
    drive.flush_and_unmount()
    print("flushed and unmounted Google Drive", flush=True)


def main() -> None:
    project = Path(os.environ.get("MPRINT_DRIVE_ROOT", "/content/drive/MyDrive/mprint"))
    work = Path(os.environ.get("MPRINT_WORK", "/content/mprint-work"))
    config = os.environ.get("MPRINT_CONFIG", "sources.yaml")
    only = os.environ.get("MPRINT_ONLY", "").split()
    build = os.environ.get("MPRINT_BUILD", "1") != "0"
    run = os.environ.get("MPRINT_RUN") or time.strftime("%Y%m%d-%H%M%S")

    if not (project / config).is_file():
        raise ValueError(
            f"{project / config} not found. Run `colab drivemount` first, "
            "and set MPRINT_DRIVE_ROOT if the project folder lives elsewhere."
        )
    published = project / "outputs" / run
    if published.exists():
        raise ValueError(f"{published} already exists; set MPRINT_RUN to a new name")

    started = time.monotonic()
    install(project)
    from mprint_workflow.prepare_dictionaries import prepare

    copied = sync(project, work / "input", referenced(project, config, only))
    print(f"staged {copied / 1e6:,.1f} MB from {project}", flush=True)
    output = work / "outputs" / run
    counts = prepare((work / "input" / config).resolve(), output, only, build)
    for table, rows in sorted(counts.items()):
        print(f"{table}: {rows:,} rows", flush=True)

    shutil.copytree(output, published)
    for path in output.rglob("*"):
        copy = published / path.relative_to(output)
        if path.is_file() and copy.stat().st_size != path.stat().st_size:
            raise OSError(f"{copy} differs in size from {path}; rerun with a new MPRINT_RUN")
    print(f"published {published} after {time.monotonic() - started:,.0f} s", flush=True)
    if os.environ.get("MPRINT_UNMOUNT", "1") != "0":
        flush_drive()


if __name__ == "__main__":
    main()
