# Colab scripts

These two scripts run `mprint-prepare` on a Google Colab VM against a project folder in Google Drive. The step-by-step guide is [Run the dictionary build on Google Colab with Google Drive](../docs/colab-google-drive.md).

| File | Runs on | Purpose |
| --- | --- | --- |
| `run_on_colab.sh` | Your computer | Creates a VM, mounts Drive, runs `colab_job.py`, and stops the VM. |
| `colab_job.py` | The Colab VM | Installs the wheel, copies the selected inputs to local disk, builds, and publishes the output to Drive. |

## `run_on_colab.sh`

```text
colab/run_on_colab.sh [SOURCE ...]
```

Each `SOURCE` is a key under `sources:` in the project's `sources.yaml`. With no `SOURCE`, every source is built. The script runs these commands in order:

```text
colab new -s $MPRINT_SESSION [--high-mem]
colab drivemount -s $MPRINT_SESSION
colab exec -s $MPRINT_SESSION -f colab/colab_job.py --timeout 0 [--env NAME=VALUE ...]
colab stop -s $MPRINT_SESSION
```

`colab stop` runs when the script exits, including after a failed build, unless `MPRINT_KEEP=1`. The script exits with the status of the first command that fails.

| Variable | Default | Effect |
| --- | --- | --- |
| `MPRINT_SESSION` | `mprint` | Colab CLI session name. |
| `MPRINT_HIGH_MEM` | `0` | `1` adds `--high-mem` to `colab new`. Requires Colab Pro or Pro+. |
| `MPRINT_KEEP` | `0` | `1` leaves the VM running after the script exits. Stop it with `colab stop -s mprint`. |

The script forwards `MPRINT_DRIVE_ROOT`, `MPRINT_CONFIG`, `MPRINT_BUILD`, `MPRINT_RUN`, `MPRINT_WHEEL`, and `MPRINT_UNMOUNT` to the job when they are set.

## `colab_job.py`

| Variable | Default | Effect |
| --- | --- | --- |
| `MPRINT_DRIVE_ROOT` | `/content/drive/MyDrive/mprint` | Project folder on the mounted Drive. |
| `MPRINT_CONFIG` | `sources.yaml` | Configuration file, relative to the project folder. |
| `MPRINT_ONLY` | all sources | Space-separated source names, passed to `prepare` as `only`. |
| `MPRINT_BUILD` | `1` | `0` skips the DuckDB bundle. |
| `MPRINT_RUN` | current time, `YYYYMMDD-HHMMSS` | Output folder name under `outputs/`. Must not exist yet. |
| `MPRINT_WHEEL` | newest `wheels/*.whl` by modification time | Wheel to install, relative to the project folder. With no wheel, the job uses the installed package. |
| `MPRINT_WORK` | `/content/mprint-work` | Local working folder on the VM. |
| `MPRINT_UNMOUNT` | `1` | `0` leaves Drive mounted after the job and skips the flush. Stop the VM only after the files show up in Drive. |

The job runs these steps:

1. Checks that `MPRINT_CONFIG` exists in the project folder and that `outputs/<run>` does not.
2. Installs the wheel with the `excel` extra, then force-reinstalls the wheel alone so a rebuilt wheel with the same version replaces the old one.
3. Copies the configuration file and every relative `path`, `mrconso`, `mrsty`, `descriptors`, `supplemental`, and `applications` value of the selected sources to `<work>/input`. A directory value, such as an Athena folder, is copied whole. Dot files are skipped, and a file whose size and modification time match the local copy is not copied again. If the job cannot read the configuration, it copies the whole project folder except `outputs/` and `wheels/`, and `prepare` reports the problem.
4. Runs `prepare(<work>/input/<config>, <work>/outputs/<run>, only, build)`.
5. Copies `<work>/outputs/<run>` to `<project>/outputs/<run>` and compares file sizes.
6. Calls `google.colab.drive.flush_and_unmount()` unless `MPRINT_UNMOUNT=0`.

Relative input paths in `sources.yaml` resolve inside `<work>/input`, so the build reads local copies. An absolute `/content/drive/...` path reads from the Drive mount directly.

| Message | Meaning |
| --- | --- |
| `installed <wheel>` | Step 2 finished. |
| `staged <N> MB from <project>` | Step 3 copied `N` MB. `0.0 MB` means every input was already on the VM. |
| `<source>: <N> concepts` | One source finished. |
| `<table>: <N> rows` | Row count of a published table. |
| `published <path> after <N> s` | Step 5 finished. |
| `flushed and unmounted Google Drive` | Step 6 finished. |

On failure the job raises an exception and `colab exec` prints the traceback. An error from `prepare` starts with `source '<name>':` and names the input that failed.
