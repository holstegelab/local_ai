#!/usr/bin/env bash
# This matching step reads full ID columns and does not load Llama or require a GPU.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
export RD_TASK=catalog
exec bash "$script_dir/run_rd_llama.sh" "$@"
