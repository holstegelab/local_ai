#!/usr/bin/env bash
# Run inside the compute allocation; the mount and inference share the same host.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
remote=${RD_REMOTE:-RD:/100plus_staff/data/}
python_bin="$script_dir/conda-env/bin/python"
for program in rclone mountpoint; do
    command -v "$program" >/dev/null || { echo "Missing required program: $program" >&2; exit 1; }
done
if command -v fusermount3 >/dev/null; then
    unmount_bin=$(command -v fusermount3)
elif command -v fusermount >/dev/null; then
    unmount_bin=$(command -v fusermount)
else
    echo "Missing fusermount3/fusermount; cannot manage the job's ResearchDrive mount." >&2
    exit 1
fi
[[ -x "$python_bin" ]] || { echo "Conda environment missing: $python_bin" >&2; exit 1; }
if [[ ${RD_TASK:-llama} == catalog || ${RD_TASK:-llama} == fair ]]; then
    if ! "$python_bin" -c 'import openpyxl, defusedxml' </dev/null 2>/dev/null; then
        "$python_bin" -m pip install --no-input 'openpyxl>=3.1,<4' 'defusedxml>=0.7,<1' </dev/null
    fi
fi
# Do not allow forwarded arguments to replace this launcher's protected source.
for argument in "$@"; do
    case "$argument" in
        --root|--root=*|--allow-non-mount|--summarize-existing|--summarize-existing=*|--metadata-only|--check)
            echo "This launcher manages a live source mount; unsupported argument: $argument" >&2
            exit 1 ;;
    esac
done
umask 077
job_dir=$(mktemp -d "${TMPDIR:-/tmp}/rd-llama.XXXXXXXX")
mount_dir="$job_dir/researchdrive"
mkdir "$mount_dir" "$job_dir/cache"
cleanup() {
    status=$?
    trap - EXIT
    if mountpoint -q "$mount_dir"; then
        if ! "$unmount_bin" -u "$mount_dir"; then
            echo "Could not unmount $mount_dir; inspect it on this node." >&2
            if [[ $status -eq 0 ]]; then status=1; fi
        fi
    fi
    # Leave logs/cache in this private job directory; never recursively delete a mount.
    echo "Mount log and read cache: $job_dir"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Mounting ResearchDrive read-only on %s\n' "$(hostname)"
rclone mount "$remote" "$mount_dir" \
    --read-only --vfs-cache-mode full --cache-dir "$job_dir/cache" \
    --vfs-cache-max-size 5G --buffer-size 1M --vfs-read-ahead 0 \
    --vfs-read-chunk-size 4M --vfs-read-chunk-size-limit 4M \
    --daemon --daemon-wait 60s --log-file "$job_dir/rclone.log" </dev/null
if ! mountpoint -q "$mount_dir"; then
    echo "ResearchDrive did not mount. See $job_dir/rclone.log" >&2
    exit 1
fi
if [[ ${RD_TASK:-llama} == catalog ]]; then
    "$python_bin" "$script_dir/sample_catalog.py" --root "$mount_dir" "$@"
elif [[ ${RD_TASK:-llama} == fair ]]; then
    "$python_bin" "$script_dir/fair_extract.py" --root "$mount_dir" \
        --gpu-layers -1 "$@"
else
    "$python_bin" "$script_dir/rd_inventory.py" --root "$mount_dir" \
        --gpu-layers -1 --read-drive --review-files "$@"
fi
