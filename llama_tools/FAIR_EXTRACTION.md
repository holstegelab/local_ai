# Extract brain, blood and genetic-assay metadata with local Llama

This extends the existing person catalogue with **proposed** specimen and assay
field meanings. Llama interprets a bounded sample of metadata; Python reads the
selected table rows and attaches only existing confirmed person links. Unknown
or unlinked specimens stay visible. This does not establish a validated matched
cohort until the source meanings and links have been reviewed.

The source ResearchDrive and the input catalogue are not modified. Each run
writes to a new private output directory. Sensitive outputs must remain in an
appropriately restricted research location.

Raw data protection is enforced by the read-only ResearchDrive mount and isolated
reader. The FAIR Llama worker receives no raw-data mount and no write tools or
credentials. The startup check verifies rejection of create, overwrite, append,
rename, delete, directory creation, permission changes and truncation using only a
marked disposable fixture. If any check fails, extraction stops. Corrections and
normalized values are saved only in new derived reports outside the source drive.

## Files to keep together on Spider

Clone this repository on Spider and use its `llama_tools/` directory, or copy
the scripts into your existing script directory. Preserve site-specific edits
before replacing earlier versions. Model weights, conda environments, source
maps and specimen rows are not included.

Keep `fair_extract.py`, `fair_extraction_prompt.txt`, `rd_inventory.py`, and
`sample_catalog.py` in the same directory. For automatic mounting, also keep
`run_rd_fair.sh` and the updated `run_rd_llama.sh`. Reuse the existing
`conda-env` beside those scripts. The extraction reader needs `openpyxl` and
`defusedxml`, as does the existing sample catalogue.

The task and interpretation rules are in [FAIR_EXTRACTION_TASK.md](FAIR_EXTRACTION_TASK.md).
Weights and download provenance are described in [FAIR_MODEL.md](FAIR_MODEL.md).

## Inspect source selection without Llama or a ResearchDrive mount

Supply the path to your existing private catalogue on the execution host:

```bash
python3 fair_extract.py \
  --catalog /path/to/private/catalogue \
  --output fair_runs/schema_plan_01 \
  --plan-only
```

Use paths that exist on the execution host. Without `--source-map`, selection
uses relevant headers from the catalogue. To restrict it, supply your own private
TSV with `source_file`, `sheet` and 1-based `column_number` columns using
`--source-map /path/to/private/source_fields.tsv`. Review the plan before extraction.

## Download the recommended model once

Run in your existing script directory on Spider:

```bash
cd /project/holstegelab/Share/llama
python3 setup_llama.py --download-model-only \
  --model-repo bartowski/Meta-Llama-3.1-8B-Instruct-GGUF \
  --model-file Meta-Llama-3.1-8B-Instruct-Q5_K_M.gguf \
  --revision bf5b95e96dac0462e2a09145ec66cae9a3f12067
```

This verifies the published checksum and records the exact model revision. It
does not reinstall the working conda/CUDA environment. The weights are about
5.73 GB; an existing verified file is reused. No weights are included with the
scripts.

## Run inside an allocated GPU session

Use your normal compute-allocation method. The launcher contains no scheduler
commands. It mounts ResearchDrive read-only on that same node, runs extraction,
and unmounts on exit:

```bash
cd /project/holstegelab/Share/llama
bash run_rd_fair.sh \
  --catalog "$PWD/catalogs/YOUR_CATALOGUE" \
  --output "$PWD/fair_runs/pilot_01" \
  --model "$PWD/models/Meta-Llama-3.1-8B-Instruct-Q5_K_M.gguf"
```

The launcher uses the existing `rclone` credentials and the same remote as the
inventory (`RD:/100plus_staff/data/`). Set `RD_REMOTE` if your remote differs.
Credentials are not passed to the isolated reader or model. The launcher may
install the two small XLSX reader dependencies if they are missing.

For an already-mounted drive:

```bash
./conda-env/bin/python fair_extract.py \
  --catalog "$PWD/catalogs/YOUR_CATALOGUE" \
  --output "$PWD/fair_runs/pilot_01" \
  --root /actual/read-only/researchdrive/mount \
  --model "$PWD/models/Meta-Llama-3.1-8B-Instruct-Q5_K_M.gguf" \
  --gpu-layers -1
```

Use a mount that exposes the same relative paths as the catalogue. Choose a new
`--output` directory outside the source drive if you need a specific output
location. Local inference uses the existing read-only, network-isolated
`bubblewrap` protection; there is no writable-source fallback.

Output directories must be new: choose `pilot_02` for a second run, for example.
The GPU/dependency preflight now runs before reading source tables or creating
output files, so a run on a login node fails early.
This is a pilot rather than an inventory of every possible brain/blood/genetics
source. Inspect source selection with `--plan-only` first, and use a private
`--source-map` when you need to restrict selected tables or columns.

## Continue after extraction succeeded but model inference failed

If the old run has zero read errors and contains `source_plan.json`,
`model_inputs.json`, `observed_rows.jsonl`, `links.sqlite` and `summary.json`, reuse
that saved extraction in a **new** output directory:

```bash
bash run_rd_fair.sh \
  --resume-from "$PWD/fair_runs/pilot_01" \
  --output "$PWD/fair_runs/pilot_02"
```

Run this inside an allocated GPU session, as for the initial run. Resume does not
mount ResearchDrive, reread raw tables, or change `pilot_01`. It copies the saved
extraction and link database into the new run, then performs Llama interpretation
and creates the overview. The result describes the source snapshot from the
original extraction time; it does not verify later source changes. The new
summary records the old run and its extraction timestamp. An incomplete extraction
or a database with an active journal is rejected. Omit `--catalog`, `--source-map`
and `--root` when resuming.

## Outputs

- `source_plan.json`: exact selected tables and source-column positions.
- `proposals.jsonl`: Llama's proposed bindings, term mappings, evidence and gaps.
- `observed_rows.jsonl`: all-row evidence for selected columns, including exact IDs; private.
- `extracted_fields.jsonl`: raw fields, proposed meanings and source file/sheet/row/column.
- `row_links.jsonl`: verified existing catalogue links and unresolved rows.
- `person_overview.csv`: provisional brain-region labels, materials and assay evidence by linked person.
- `summary.json` and `errors.jsonl`: coverage, settings, failures and review requirements.

The overview reports region-label counts, not physical specimen counts. Its triple
matches are candidates requiring review; they do not establish current sample stock
or verified genetic data availability. Multiple copied tables can support the same
specimen, and a region abbreviation may duplicate a longer anatomical label.

## Review before selecting people

Check the run summary for coverage and errors. Review Llama's column bindings and
exact-value mappings, especially `Participant ID`, brain specimen/block fields,
material labels, and genetic status flags. An ID or data path does not establish
that an assay completed or that its files are readable.

Check whether your catalogue has confirmed links for each required material
category. Biobank participant namespaces and brain specimen links may need
explicit review. If links are missing, enrich the authoritative mapping or
catalogue rules and rerun the person catalogue before relying on a
brain/blood/genetics intersection.

Inspect raw evidence for a few donors with multiple brain specimens and assays.
Keep brain regions, blocks and aliquots separate. Region labels need documented
normalization before distinct labels can be interpreted as distinct anatomical
regions. Preserve unreviewed, unknown and unlinked records.

This change was checked locally with catalogue metadata and small synthetic
examples. Actual GPU inference and source-table extraction must be validated on
Spider; those have not been run here.

To rerun the focused checks without ResearchDrive or a model:

```bash
python3 -m unittest discover -s . -p test_fair_extract.py -v
```

Checks cover invalid model JSON/terms, changed or conflicting source IDs, ID
namespaces, leading zeroes, formula-cell exclusion, withheld identifier previews,
negative/planned assay flags, schema selection and resume behavior. Synthetic
checks use mocked reader/model workers. They do not establish real model accuracy
or runtime access to source files.
