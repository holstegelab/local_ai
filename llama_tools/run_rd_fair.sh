#!/usr/bin/env bash
# Mount ResearchDrive read-only inside the current compute session.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
for argument in "$@"; do
    case "$argument" in
        --resume-from|--resume-from=*)
            exec "$script_dir/conda-env/bin/python" "$script_dir/fair_extract.py" \
                --gpu-layers -1 "$@" ;;
    esac
done
export RD_TASK=fair
exec bash "$script_dir/run_rd_llama.sh" "$@"
