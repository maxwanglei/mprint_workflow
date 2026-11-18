#!/usr/bin/env bash
# Build the MPRINT dictionary on a new Colab VM from your Google Drive project folder.
#
# Usage: colab/run_on_colab.sh [SOURCE ...]
#
# With no SOURCE, every source in the project's sources.yaml is built.
# Settings are environment variables; colab/README.md lists them.
set -euo pipefail

session="${MPRINT_SESSION:-mprint}"
job="$(cd "$(dirname "$0")" && pwd)/colab_job.py"

new=(colab new -s "$session")
if [[ "${MPRINT_HIGH_MEM:-0}" == 1 ]]; then
	new+=(--high-mem)
fi

settings=()
if [[ $# -gt 0 ]]; then
	settings+=(--env "MPRINT_ONLY=$*")
fi
for name in MPRINT_DRIVE_ROOT MPRINT_CONFIG MPRINT_BUILD MPRINT_RUN MPRINT_WHEEL MPRINT_UNMOUNT; do
	if [[ -n "${!name:-}" ]]; then
		settings+=(--env "$name=${!name}")
	fi
done

"${new[@]}"
if [[ "${MPRINT_KEEP:-0}" != 1 ]]; then
	trap 'colab stop -s "$session"' EXIT
fi
colab drivemount -s "$session"
colab exec -s "$session" -f "$job" --timeout 0 ${settings[@]+"${settings[@]}"}
