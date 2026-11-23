# Run the dictionary build on Google Colab with Google Drive

This guide runs `mprint-prepare` on a Google Colab VM that you control from your terminal with the [Colab CLI](https://github.com/googlecolab/google-colab-cli). Your configuration, release files, and finished dictionaries live in one Google Drive folder. Nothing large has to sit on your computer, and the VM is released when the build ends.

One build works like this:

1. `colab new` creates a VM.
2. `colab drivemount` mounts your Drive on the VM at `/content/drive`.
3. `colab exec` runs [`colab/colab_job.py`](../colab/colab_job.py) on the VM. The job installs the project wheel from Drive and copies `sources.yaml` and the release files of the sources you build to the VM's local disk. It builds the dictionary there and copies the result to `outputs/<run>` in your Drive folder.
4. `colab stop` releases the VM.

The build itself never writes to the Drive mount. Its SQLite and DuckDB scratch files need a local disk, which is why the job copies inputs first and publishes the finished output last.

[`colab/run_on_colab.sh`](../colab/run_on_colab.sh) runs all four steps for you. [`colab/README.md`](../colab/README.md) lists every setting of both scripts.

## Check what you need

- A Mac or Linux computer. The Colab CLI does not support Windows.
- A Google account that can use Colab. The default CPU runtime is enough for the public releases. `--high-mem` needs Colab Pro or Pro+.
- [uv](https://docs.astral.sh/uv/) to install the CLI and build the wheel.
- This repository on your computer.
- The release files you plan to import. UMLS and DrugBank need a license from their publishers.

## Set up once

### Install and sign in to the Colab CLI

1. Install the CLI:

	```bash
	uv tool install google-colab-cli
	colab version
	```

	`colab version` prints `Version: 0.8.0` or later.

2. Sign in by running a command that needs your account, such as the one that shows your compute-unit balance:

	```bash
	colab usage
	```

	The CLI prints a sign-in URL. Open it, choose your Google account, and paste the code that Google shows back into the terminal. The CLI stores the token in your operating system's keyring, so later commands do not ask again.

If your Linux keyring is locked or missing, sign in with Application Default Credentials instead and add `--auth adc` to every `colab` command:

```bash
gcloud auth application-default login \
	--scopes=openid,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/colaboratory
```

### Build the project wheel

From the repository root, build the wheel that the VM installs:

```bash
uvx --from build pyproject-build
```

The command writes `dist/mprint_drug_dictionaries-0.1.0-py3-none-any.whl` and a source archive. Only the wheel goes to Drive.

### Create the Drive project folder

Create a folder named `mprint` at the top of **My Drive** with this layout:

```text
mprint/
  sources.yaml
  wheels/
    mprint_drug_dictionaries-0.1.0-py3-none-any.whl
  raw/
    ncit/Thesaurus.txt
    gsrs/UNIIs.zip
    ...
  outputs/            # created by the first build
```

You can upload in the Drive web page. With Google Drive for desktop on a Mac, the folder is also on your computer at `~/Library/CloudStorage/GoogleDrive-<your address>/My Drive/mprint`, so you can copy files there in Finder or with `cp`.

To keep the project folder somewhere else, such as a shared drive, set `MPRINT_DRIVE_ROOT` to its path on the VM. A shared drive mounts at `/content/drive/Shareddrives/<drive name>`.

### Add the release files

Put each release you have under `raw/`. The paths below match [`sources.example.yaml`](../sources.example.yaml). [FORMATS.md](../FORMATS.md) describes each format and links its publisher.

| Source key | File | Path under `mprint/` |
| --- | --- | --- |
| `athena` | The extracted Athena vocabulary download | `raw/athena/` (contains `CONCEPT.csv` and the other tables) |
| `umls` | `META/MRCONSO.RRF` and `META/MRSTY.RRF` from the UMLS Metathesaurus | `raw/umls/META/` |
| `mesh` | The descriptor and supplementary-record XML files | `raw/mesh/desc.xml`, `raw/mesh/supp.xml` |
| `gsrs` | [`UNIIs.zip`](https://precision.fda.gov/uniisearch/archive/latest/UNIIs.zip), and optionally [`UNII_Data.zip`](https://precision.fda.gov/uniisearch/archive/latest/UNII_Data.zip) for RxNorm, NCIt and PubChem codes | `raw/gsrs/UNIIs.zip`, `raw/gsrs/UNII_Data.zip` |
| `ncit` | `Thesaurus.txt` from [`Thesaurus.FLAT.zip`](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/Thesaurus.FLAT.zip) | `raw/ncit/Thesaurus.txt` |
| `drugbank` | The full database XML, or its zip | `raw/drugbank/full database.xml` |
| `drugsfda` | `Products.txt` and `Applications.txt` from the [Drugs@FDA zip](https://www.fda.gov/media/89850/download) | `raw/drugsfda/` |
| `orange_book` | `products.txt` from the [Orange Book zip](https://www.fda.gov/media/76860/download) | `raw/orangebook/products.txt` |
| `article57` | The [EMA Article 57 workbook](https://www.ema.europa.eu/en/documents/other/article-57-product-data_en.xlsx) | `raw/ema/article57.xlsx` |

To cut upload time, compress `MRCONSO.RRF`, `MRSTY.RRF`, and the MeSH XML files with `gzip` before you upload them. The readers accept the `.gz` files directly. Add `.gz` to their paths in `sources.yaml`.

You can also download the public releases on the VM straight into Drive, which skips your network connection. Do this in a session where Drive is mounted, before the build:

```bash
colab new -s mprint
colab drivemount -s mprint
colab exec -s mprint --timeout 0 <<'EOF'
!mkdir -p /content/drive/MyDrive/mprint/raw/ncit
!curl -sSL -o /tmp/ncit.zip https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/Thesaurus.FLAT.zip
!unzip -o -q /tmp/ncit.zip Thesaurus.txt -d /content/drive/MyDrive/mprint/raw/ncit
EOF
```

Lines that start with `!` run as shell commands on the VM. Keep the session for the build with `MPRINT_KEEP=1`, or stop it with `colab stop -s mprint`.

### Write `sources.yaml`

1. Copy [`sources.example.yaml`](../sources.example.yaml) to `mprint/sources.yaml`.
2. Delete every source you do not have files for.
3. Set each `release` to the actual release identifier or snapshot date. Quote dates and numeric values, for example `release: "2026-11-17"`.
4. Keep every path relative, such as `raw/ncit/Thesaurus.txt`. Relative paths resolve against the copy of the folder on the VM's local disk. An absolute `/content/drive/...` path makes the build read from Drive directly, which is slower.
5. For `drugsfda`, keep `applications: raw/drugsfda/Applications.txt`. Without it, the product evidence has no application type or sponsor.

## Run a build

### Run every step with the wrapper script

From the repository root on your computer, build every source in `sources.yaml`:

```bash
colab/run_on_colab.sh
```

To build some sources only, name their keys from `sources.yaml`:

```bash
colab/run_on_colab.sh ncit gsrs drugsfda
```

During `colab drivemount`, the CLI opens a Google consent page in your browser and prints its URL. Allow Drive access, return to the terminal, and press Enter. The script then runs the job. With the November 2026 public releases, the output looks like this:

```text
installed mprint_drug_dictionaries-0.1.0-py3-none-any.whl
staged 118.3 MB from /content/drive/MyDrive/mprint
ncit: 27,843 concepts
gsrs: 171,917 concepts
drugsfda: 52,021 concepts
concepts: 251,781 rows
...
published /content/drive/MyDrive/mprint/outputs/20261118-163000 after <N> s
flushed and unmounted Google Drive
```

The script stops the VM when the job ends, whether the build succeeded or failed.

### Run each step yourself

Use separate commands when you want to inspect the VM between steps or keep it for another build:

```bash
colab new -s mprint
colab drivemount -s mprint
colab exec -s mprint -f colab/colab_job.py --timeout 0 --env MPRINT_ONLY="ncit gsrs"
colab stop -s mprint
```

Always pass `--timeout 0` to `colab exec`. Without it, `colab exec` fails after 30 seconds without output, and a large source can read for longer than that before it prints anything.

The job unmounts Drive when it finishes. To run a second job in the same session, run `colab drivemount -s mprint` again first.

## Use the results

Each build writes one folder in Drive:

```text
mprint/outputs/<run>/
  concepts.csv
  synonyms.csv
  relationships.csv
  xrefs.csv
  product_evidence.csv     # only with FDA or EMA sources
  vocabularies.yaml
  bundle/
    lexicon.duckdb         # only without MPRINT_BUILD=0
    manifest.json
```

`<run>` is the start time, such as `20261118-163000`. To pick the name, set `MPRINT_RUN=<name>`. The job refuses to start if `outputs/<name>` already exists, so a finished build is never overwritten.

To query the bundle from your computer, open it through Google Drive for desktop or download it from the Drive web page. Then follow [Look up a reported name](../README.md#look-up-a-reported-name).

To download a build without Drive for desktop, keep the VM, zip the local copy, and download the zip:

```bash
MPRINT_KEEP=1 MPRINT_RUN=nov-build colab/run_on_colab.sh
echo 'import shutil; shutil.make_archive("/content/mprint-work/outputs/nov-build", "zip", "/content/mprint-work/outputs", "nov-build")' | colab exec -s mprint --timeout 0
colab download -s mprint /content/mprint-work/outputs/nov-build.zip ./nov-build.zip
colab stop -s mprint
```

## Plan for large releases

On a laptop during testing, the November 2026 NCIt, UNII, Drugs@FDA, and Orange Book releases built together in 31 seconds, bundle included, and the EMA workbook in 21 seconds. On Colab, the copy from Drive adds time, and the CPU runtime has fewer cores. The build time on Colab has not been measured.

UMLS is the largest input. Before a UMLS build, check the VM's disk and memory:

```bash
colab new -s mprint
echo '!df -h /content; free -h' | colab exec -s mprint
```

The job keeps a local copy of every input it builds plus the scratch databases, so free disk must exceed about twice the size of those inputs. If memory runs short, start the VM with more RAM. This needs Colab Pro or Pro+:

```bash
MPRINT_HIGH_MEM=1 colab/run_on_colab.sh umls
```

For a build that runs for an hour or more:

- Run the wrapper inside `tmux` or `screen`, so a closed terminal window does not end `colab exec`. The CLI does not document what happens to the remote job if your terminal disconnects.
- On a Mac, keep the computer awake with `caffeinate -i colab/run_on_colab.sh`.
- Check the VM from another terminal with `colab status -s mprint`, and check your compute units with `colab usage`.

## Update the code on Drive

After you change the code:

1. Rebuild the wheel with `uvx --from build pyproject-build`.
2. Copy the new wheel into `mprint/wheels/`.

The job installs the newest wheel in `wheels/` by modification time and replaces any installed copy, even when the version number did not change. To pin a wheel, set `MPRINT_WHEEL=wheels/<file name>`.

## Fix common problems

- If `colab` asks you to sign in on every command on Linux, your keyring is locked. Use `--auth adc` as described in [Install and sign in to the Colab CLI](#install-and-sign-in-to-the-colab-cli).
- If `colab drivemount` seems to hang, finish the consent page in your browser, then press Enter in the terminal. Without a browser on the computer, open the printed URL on another device.
- If the job reports `sources.yaml not found`, Drive is not mounted or the project folder is elsewhere. Run `colab drivemount -s mprint`, or set `MPRINT_DRIVE_ROOT`.
- If the job reports `outputs/<run> already exists`, set a new `MPRINT_RUN`.
- If `colab exec` stops with a timeout, add `--timeout 0`.
- If the job reports `No space left on device`, the VM disk is full. Build fewer sources at once, or compress the large inputs with `gzip`.
- If an error starts with `source '<name>':`, that source's file or settings are wrong. [FORMATS.md](../FORMATS.md) lists what each format expects.
- If a second `colab exec` in the same session cannot find Drive, the first job unmounted it. Run `colab drivemount -s mprint` again.
- If a build finished but its files are missing from Drive, you ran the job with `MPRINT_UNMOUNT=0` and stopped the VM before Drive caught up. Run the build again with the default setting.
