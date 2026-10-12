# Local Llama ResearchDrive inventory

## Brain specimens, blood materials and genetic assays

Use [FAIR_EXTRACTION.md](FAIR_EXTRACTION.md) for the local Llama metadata
extraction task, prompt, recommended 8B weights and read-only launcher. It
preserves source evidence and existing confirmed person links, and keeps
interpretations provisional until reviewed.

Run these scripts on the Linux host where ResearchDrive is mounted. The examples
use Spider; replace site-specific paths and allocation options for your environment.

## Person-level ID matching and data availability

For one row per person with linked IDs and separate blood/brain/plasma/data-type
columns, use `bash run_rd_catalog.sh`. This reads full ID columns from the
authoritative key directory and source tables, and does not need a GPU. See
[SAMPLE_CATALOG.md](SAMPLE_CATALOG.md) for execution, evidence tables, matching
rules and skipped locked files. Locked workbooks are never unlocked.

## Setup

Requirements: Linux, Python 3.10+ for the setup script, conda, outbound HTTPS for
installation, and **bubblewrap (`bwrap`) with unprivileged user namespaces enabled**.
The setup checks bubblewrap before downloading anything. If the check fails, ask
Spider support to provide/enable bubblewrap. There is no unsafe fallback.

After loading/initializing your existing conda installation on Spider:

```bash
cd /home/holstegelab-gozhegov/Share/llama
python3 setup_llama.py --backend cuda
```

Run CUDA setup in a session with an allocated **NVIDIA GPU**. It selects an official
CUDA wheel using the driver's reported supported CUDA version and, when available,
GPU compute capability. `CUDA_VISIBLE_DEVICES` is preserved for inference so your
session's GPU selection is respected. No scheduler commands are built into the scripts.

This creates `conda-env/`, installs `llama-cpp-python` for CUDA use, and downloads
Llama 3.2 3B Instruct Q4_K_M (~2 GB) into `models/`. The smoke test confirms that
model layers were offloaded to the GPU; an unconfirmed offload fails setup.
The GPU build replaces an existing CPU build in this dedicated environment.
Setup also installs the matching NVIDIA CUDA runtime and cuBLAS pip packages.
The driver-reported CUDA version does not mean these runtime libraries are installed.
The sandbox discovers their library directories inside `conda-env` automatically.
No system service or root access is required.
Allow roughly 6–8 GB RAM and several GB of disk space for installation/inference.
GPU memory usage depends on the model/context; this small model needs several GB
VRAM. The default also uses up to four CPU threads.

To choose a wheel explicitly, use e.g. `--backend cuda --cuda-wheel cu125`; only do
this when the driver supports that CUDA version. If no compatible wheel can be
downloaded, load your existing Spider CUDA toolkit (with `nvcc` available), then use:

```bash
python3 setup_llama.py --backend cuda --cuda-wheel source
```

The source option installs conda build tools and enables `GGML_CUDA`. It does not
install a CUDA toolkit or system driver. For CPU use, run `python3 setup_llama.py
--backend cpu` instead; compatible official CPU wheels are preferred, with a source
build as a fallback.

Older bubblewrap versions are supported: Python clears the subprocess environment
directly, so `bwrap --clearenv` is not required. Read-only mounts, capability dropping,
and network isolation are retained. The same startup protection check still runs.

If an earlier installation reports `libcudart.so.13` or `libcublas.so.13` missing,
repair it inside a GPU allocation without replacing Llama or re-downloading the model:

```bash
python setup_llama.py --backend cuda --cuda-wheel cu132 --repair-runtime
```

CUDA 13.2 setup selects runtime 13.2 and cuBLAS 13.3 (NVIDIA component versions
differ from toolkit versions). Repair finishes by checking inference and actual
GPU offload. Read-only drive protection remains enabled.

The download is pinned to the resolved Hugging Face commit and checked against its
published SHA256. `setup-*.json` records the model revision/checksum and installed
package versions. Existing model files are verified and never silently replaced.
Interrupted downloads are cleaned up; rerunning restarts the model download.
The script finishes with a sandbox test and a small real inference test.
The weights are a third-party quantization of Meta Llama and retain the model's
license terms; see the model card below.

## Review every XLSX, CSV and TXT file read-only

Your GPU installation already passed its smoke check; no reinstall or new Python
packages are needed. The launcher mounts ResearchDrive **inside the GPU job**, scans
all file metadata, then explicitly reads a small preview of **every `.xlsx`, `.csv`,
and `.txt` file** in all subfolders. File selection does not depend on Llama making
a tool request. Llama receives each actual preview and writes a per-file overview.

```bash
cd /project/holstegelab/Share/llama
srun --partition=gpu_a100_22c --time=02:00:00 --gpus=a100:1 \
  --ntasks=1 --cpus-per-task=4 --mem=12G \
  bash run_rd_llama.sh
```

A per-file review can take substantial time; adjust the requested job duration
to the number of files and your cluster's limits. Progress and each review
are saved as they arrive. An interrupted run preserves partial results and records
`review_complete: false` in `review_summary.json` if the process can handle the signal.
A hard termination may leave that file absent; per-file JSONL/CSV records still remain.

For a short pilot instead, forward a file limit:

```bash
# Inside your GPU allocation:
bash run_rd_llama.sh --review-limit 20
```

The default is no limit. Files are selected in relative path order, including nested
folders. Files of other formats remain in the metadata inventory. The report has:

- `overview.md`: exact inventory totals and review coverage, linking to the detailed reviews.
- `file_reviews.md`: a section per selected file, with its content overview or error.
- `file_reviews.csv`: path, format, review status, overview, and error; convenient to filter.
- `file_reviews.jsonl`: exact paths and review records, streamed one per line.
- `review_summary.json`: matched/selected/processed counts, preview successes, read/model
  failures, and completion status. Failed reads do not stop the remaining file reviews.
- `accesses.jsonl`: the actual file-read attempts, errors and byte counts where available;
  no raw preview content is stored in this audit.

These are **bounded content previews**, not full-file validation:

- Text/CSV previews read up to 4096 bytes and 20 lines. CSV delimiter detection supports
  comma, semicolon, tab and pipe. UTF-8 and BOM-marked UTF-16/UTF-32 are supported;
  a CP1252 fallback is labeled as inferred. Formulas in CSV are treated as text.
- XLSX previews stream up to three worksheets in workbook order, eight rows and eight
  populated cells per row, with 200 characters per cell. Worksheet names are supplied
  to Llama. Cached/raw cell values are used; formulas and external links never execute,
  and Excel date serials are not converted. Empty sheets and read errors are explicit.
- Large worksheets are streamed. Decompressed XML reads are capped at 8 MiB per
  worksheet and 16 MiB for shared strings; workbook metadata is capped at 1 MiB per
  component, and compressed workbooks over 512 MiB are skipped with an error.
  Partial sheet/shared-string warnings are recorded in the per-file JSONL records.
- The actual model tokenizer bounds the prompt; long previews can be shortened further.
  PDF, DOCX, old XLS and other formats are not content-reviewed in this mode.

The launcher itself has no scheduler dependencies. It requires `rclone`, `mountpoint`,
and `fusermount3` or `fusermount` on the compute node, plus your existing rclone
configuration/authentication and remote access. It uses `RD:/100plus_staff/data/`;
override with `RD_REMOTE` if needed. It creates a private temporary mount/cache
directory, waits for the read-only mount, runs inference and unmounts its own mount
on exit. Its log/cache location is printed and left for diagnosis; cached source reads
can remain until the job's temporary storage is cleaned up. Rclone runs outside the
model sandbox; its credentials are not passed into the model sandbox.

If ResearchDrive is already mounted **on the GPU node**, use its actual path:

```bash
./conda-env/bin/python rd_inventory.py --root "$HOME/rd-mount" \
  --gpu-layers -1 --review-files
```

For a subfolder pilot, add `--allow-non-mount` and set `--root` to a real subfolder
of that mount. Every output directory must be new. `--review-files` implies
`--read-drive`; the launcher now enables both. `--folder-summaries` and `--tool-steps`
apply only to the older folder-summary/browsing mode and do not limit file reviews.
Without either content flag, the model receives metadata only. The optional older
`--read-drive` mode lets Llama choose tools and can finish without previewing a file;
use `--review-files` when content review is required.

## Optional metadata-only workflow

### When ResearchDrive is mounted on a different node from the GPU

An rclone FUSE mount is local to the host/mount namespace where it was started;
a mount on a login node may not be visible on the GPU node. Do not bypass the
mount-point check to scan an empty underlying directory. Also, `$PWD/rd-mount`
equals `~/rd-mount` only when the mount command was run from your home directory.
Use the actual absolute mount path for `--root`.

First run the metadata-only phase on the node where `ls ~/rd-mount/` shows the
ResearchDrive folders, keeping rclone running. This phase needs no GPU and does
not import the CUDA-enabled Llama library:

```bash
cd /project/holstegelab/Share/llama
./conda-env/bin/python rd_inventory.py \
  --root "$HOME/rd-mount" \
  --metadata-only \
  --output reports/rd-scan
```

The output directory must be new. If site rules require compute jobs for metadata
traversal, mount ResearchDrive and run this phase on an allowed CPU node instead.
This only lists directories and reads metadata; rclone's `--read-only` adds its
own protection alongside the scanner sandbox. A scan with errors exits 2 but
still saves an explicitly incomplete inventory; inspect `errors.jsonl` before
deciding to summarize it.

Then, in your GPU allocation, run:

```bash
cd /project/holstegelab/Share/llama
./conda-env/bin/python rd_inventory.py \
  --summarize-existing reports/rd-scan \
  --gpu-layers -1 \
  --output reports/rd-overview
```

This opens the saved SQLite inventory read-only and creates a **new** overview.
It does not require the original drive to be mounted and does not rescan it.
The original scan directory is preserved. `reports/rd-overview/` contains the
Llama overview, its log, and a summary linking back to the original scan metadata.
No setup or runtime repair is needed after a successful GPU smoke check.

### Metadata summaries when ResearchDrive is visible on the GPU node

```bash
./conda-env/bin/python rd_inventory.py \
  --root /home/holstegelab-gozhegov/rd-mount \
  --gpu-layers -1 \
  --threads 4
```

The default root is already `/home/holstegelab-gozhegov/rd-mount`; override it if
Spider uses another location. Whole-drive scans require a real mount point so an
unmounted, empty mount directory is not mistaken for an empty drive. Deliberate
subfolder scans use `--allow-non-mount`. A full remote-drive metadata traversal can
take a long time and generate many storage requests.

`--gpu-layers -1` requests all layers on the GPU. Omit it (or use `--gpu-layers 0`)
for CPU inference. Check GPU access without scanning with
`./conda-env/bin/python rd_inventory.py --check --gpu-layers -1`.

Reports go into a new `reports/<UTC timestamp>-<unique ID>/` beside the script:

- `overview.md`: exact totals, recursive top-level group totals, and local Llama
  interpretations of the overall inventory and up to ten top-level groups.
- `files.csv`: one record per observed file, directory, symlink, or special entry;
  path, type, logical file size in bytes, UTC modification date, and extension.
- `files.jsonl`: exact original metadata, streamed as one JSON record per line.
- `folders.csv`: every observed directory, its **direct** regular-file count and
  direct logical bytes; these counts are not recursive.
- `inventory.sqlite`: queryable full inventory, including parent/top-level groups.
- `summary.json`: exact totals, scan times, source, coverage status, and all
  top-level groups, including groups omitted from Llama's bounded interpretation.
- `errors.jsonl`, `scan.log`, `llama.log`: inaccessible/changing paths and worker
  diagnostics. `llama.log` is created only when inference runs.

All subfolders are scanned regardless of the interpretation limit. Increase
`--folder-summaries 20` if you want more top-level interpretations. In metadata mode, Llama sees only
the twenty most common extensions and at most twelve largest example paths per
summary; long example names are shortened, and tokenizer limits can reduce the
number further. Exact metadata remains in the inventory outputs. Nested directory
details remain available in `folders.csv` and the per-entry inventory.

Use `--metadata-only` for the full inventory without loading the model. This still
requires the read-only scanner sandbox. Exit status is 0 for success, 2 for a
completed scan with recorded errors/incomplete coverage, and 1 for a fatal failure.
Interrupted/failed runs preserve partial reports; a missing `summary.json` means
the scan did not complete. A failed Llama worker leaves the complete collected
inventory and a partially written overview; inspect its log.

## How source files are protected

- The scanner runs in a bubblewrap mount namespace with ResearchDrive bound at
  `/researchdrive` **read-only**. It uses directory listing and stat calls only;
  it never opens regular-file contents. Directory symlinks are not traversed.
- Llama runs in a separate sandbox with its read-only runtime, script and model,
  plus private temporary/device/process filesystems. With `--read-drive`,
  ResearchDrive is also bound **read-only** at `/researchdrive`. Without this flag,
  ResearchDrive is absent from the model sandbox.
- GPU inference additionally exposes NVIDIA device nodes and read-only driver/CUDA
  libraries. Module library directories are not mounted wholesale; only matching
  driver/CUDA shared libraries are exposed. GPU cache writes go to private `/tmp`.
- Both workers have a private network namespace, dropped capabilities, and no
  inherited credentials. The NVIDIA worker preserves only `CUDA_VISIBLE_DEVICES`
  from the compute environment; its library paths are constructed explicitly.
  No cloud model API or server is used.
- Llama receives JSON metadata through a pipe. With `--read-drive`, its only
  tools are directory listing and bounded previews. The tool handler opens paths
  relative to a pinned root directory, rejects absolute/parent paths and symlinks,
  and opens regular files read-only. There is no shell, write, delete, or execution
  tool. File contents are treated as untrusted data. Generated text is never executed.
- The report writer receives records from the scanner. Its writes go only to the
  new output directory, which must be outside the resolved ResearchDrive root.
- A startup test verifies that creating a file on a disposable read-only mount
  fails with `EROFS`. No write tests are attempted against ResearchDrive.
- If sandboxing is unavailable, the scripts stop; they do not silently run with
  write access. Use `--check --metadata-only` to check it without scanning.

These protections cover these scripts and their worker processes. They do not
change the permissions of ResearchDrive for other programs or people. Metadata
reports can themselves contain sensitive filenames and content summaries; keep them in an appropriate
restricted directory. Logical sizes count hardlinked files once per path and are
not actual disk consumption. Scanning is not a point-in-time snapshot. Unreadable
folders, special files, and skipped symlink targets are not inspected. Genome builds, sample counts, and file integrity are not determined from filenames.
Small previews are not a validation of an entire file or dataset.

## Sources

- [llama-cpp-python installation and API](https://github.com/abetlen/llama-cpp-python)
- [Model, quantization, and license](https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF)
- [Bubblewrap](https://github.com/containers/bubblewrap)
- [NVIDIA CUDA 13.2 component versions](https://docs.nvidia.com/cuda/archive/13.2.0/cuda-toolkit-release-notes/index.html)
- [NVIDIA runtime package](https://pypi.org/project/nvidia-cuda-runtime/13.2.51/)
- [Rclone mount and mount paths](https://rclone.org/commands/rclone_mount/)
