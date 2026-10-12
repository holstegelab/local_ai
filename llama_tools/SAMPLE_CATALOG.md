# Person-level IDs and data overview

This step creates **one row per person/donor**, with their linked ID schemes and
separate columns for blood, brain, plasma, feces, iPSCs, DNA, other configured
materials, and dataset/data types. It reads all rows of supported tables, rather
than the first few rows used by the Llama file reviews. It does not load Llama,
use Context7, send IDs to an external service, or require a GPU.

## Run on Spider

```bash
cd /project/holstegelab/Share/llama
srun --time=02:00:00 --ntasks=1 --cpus-per-task=4 --mem=12G \
  bash run_rd_catalog.sh
```

This requests the default partition without a GPU. Add your usual CPU partition
if Spider requires one. Inside an allocated session, run `bash run_rd_catalog.sh`.
The launcher mounts ResearchDrive read-only on that node and unmounts it on exit.
The conda environment is reused; `openpyxl` and `defusedxml` are installed only if
missing. The reader has no network or rclone credentials. Private output goes into
a new `catalogs/<timestamp>-<ID>/` directory.

If the drive is already mounted on the node:

```bash
./conda-env/bin/python sample_catalog.py --root "$HOME/rd-mount"
```

For a quick check of the readable key tables, add `--keys-only` to the launcher.
It produces the person/alias tables without scanning the data folders. A full run
also stops before the data scan if no person IDs are recognised in the keys.
A keys-only report is intentionally partial and exits 2.

The raw inventory/review outputs alone are insufficient for this table: they do
not contain all IDs. The catalogue must read the actual source tables again.

## Matching rules

1. The directory `Key files ID to ID` is the authoritative source. Every ID in a
   key-table row is interpreted as belonging to the same person. These links can
   connect several ID schemes transitively. CSV, TSV, TXT tables and every XLSX
   worksheet are supported. README files are excluded from sample matching.
2. ID headers are detected in the first 20 rows. Common donor/person headers
   identify person anchors. Specific header names become ID namespaces after
   punctuation/spacing/case normalization of the **header**. This assumes the
   same specific header refers to the same ID scheme across these cohort files;
   use explicit namespace rules when a project reuses a header for another scheme.
   Generic names such as `ID`, `Sample ID`, `Tube ID`, `Anonymized ID` and `IID`
   are scoped to their file/sheet until explicitly connected by a namespace rule.
   Headers such as `ID_100plus`, `ID_NBB` and `ID_GWAS` are recognised; their
   explicit reversed forms share namespaces (`100plusid`, `nbbid`, `gwasid`).
   Measurement names such as `IQCODE`, `amyloid`, `antacid` and `thyroid` are
   excluded. Technical batch/plate/family/gene/assay-feature IDs are not auto-used.
   In the GWAS key TSV, `ID_ORIGINAL` and `I_ID` are scoped by `Study` to prevent
   reused identifiers in different cohorts from joining people.
   `ParticipantID` and generic `ID` are not assumed to be 100-plus IDs.
3. A data-table ID is confirmed only if its namespace and exact value occur in a
   resolved key component. A matching value in another namespace becomes a
   **candidate**, even if it points to only one person; it is not auto-merged.
4. If a component contains several different person IDs in the same person
   namespace, the entire component is quarantined in `mapping_conflicts.csv`.
   Components without a recognized person anchor also remain unresolved. The
   script does not assign the first matching person arbitrarily.
5. Only surrounding whitespace is trimmed from ID values. Case, punctuation and
   leading zeros are preserved. Numeric XLSX IDs with a simple zero-padding
   format are padded accordingly; long/invalid numeric IDs are flagged rather
   than silently rounded. Cached formula values can be read without executing
   formulas; absent cached values remain missing.
6. Repeated ID columns are coalesced only when all nonmissing values agree within
   a row. Rows with conflicting repeated IDs are excluded and logged; the first
   column is never selected arbitrarily. Duplicate column positions are recorded
   in the schema/evidence.
7. Broken cosmetic Excel styles can fall back to cached worksheet XML, retaining
   readable number formats and leading-zero padding. This never rewrites a source
   workbook. An ID with an unreadable number format is excluded while later rows
   continue. Recovery notes appear in `schemas.jsonl`; `reader_warnings` counts them.

This does not use names, birth dates, fuzzy string matching, or similarity of
LLM-written descriptions to decide identity. All ID files and reports can be
sensitive and should stay in a suitably restricted location.

## Output tables

- **`person_overview.csv`**: one row per resolved person; canonical person ID,
  one column per ID namespace, separate material columns, dataset/data-type
  columns, matched file count and source files. A person known only from the
  keys is retained, with `not_linked` for unlinked datasets.
- **`id_aliases.csv`**: all IDs found in the keys, with their person/component
  status and the key file, worksheet, row and column that supplied the link.
- **`id_matches.csv`**: every confirmed data-table ID occurrence, its person,
  exact file/sheet/row/column and data-presence status.
- **`data_presence.csv`**: one person/material/data-type/source record per
  confirmed observation, including the source ID and row.
- **`unresolved_ids.csv`**: unmatched, ambiguous, conflicting and unanchored IDs.
  Candidate people are listed separately from confirmed assignments.
- **`namespace_candidates.csv`**: value-overlap counts that help review which
  source column may use which key namespace. Overlap is not an identity decision.
- **`skipped_files.jsonl`**: locked/unsupported workbooks intentionally skipped.
- **`mapping_conflicts.csv`**, **`schemas.jsonl`**, **`errors.jsonl`**: conflicts,
  detected headers/ID columns (including sheets without an ID header), and read
  issues. Inspect these before treating the catalogue as complete coverage.
- **`catalog.sqlite`**: full exact IDs, source occurrences, key links and matches.
  Use this or import CSV ID columns explicitly as text to preserve leading zeros
  in spreadsheet software. CSV formula characters are escaped on export.
- **`summary.json`**, **`config_used.json`**, **`read.log`**: counts, completion and
  matching rules, the exact config, and sandbox-reader diagnostics.

`complete` means the extraction/matching run finished; it does not mean every ID
or every source was resolved. `all_observed_ids_resolved` and the unresolved/read
counts describe linking coverage. Repeated cell warnings are limited on screen;
every individual issue remains in `errors.jsonl`. Exit 2 means review is needed; usable results
are retained. A failed/interrupted run has `complete: false` and preserves partial
SQLite/source records. Hard job termination may leave summary.json absent.

## What the material/data columns mean

- `id_listed`: this person's ID occurs in a source table in that category.
- `values_observed`: at least one explicitly configured measurement/presence
  column has a nonmissing cached value. Zero counts as an observed value.
- `no_values_observed`: ID is listed, but the configured presence columns are empty.
- `not_linked`: no confirmed link was found; this does **not** establish absence.

Without explicit presence rules, the tool reports `id_listed`, not proof of a
measured assay or a physical sample still in storage. Basic material labels come
from folders (for example Brain proteomics and Plasma proteomics). An ID in a
brain-donor registration file is a linked record, not proof of brain measurements.
`Fluid biomarkers` retains its dataset label without guessing plasma versus CSF.
Use material-column rules for mixed biomaterial manifests.

## Configure renamed ID columns or availability fields

The first run works with conservative defaults and creates detected schemas and
namespace candidates. Copy `catalog_config.example.json`, replace the example
paths/columns using the actual headers, then enable the applicable rules:

```bash
bash run_rd_catalog.sh --config /project/holstegelab/Share/llama/catalog_config.json
```

Each rule matches a relative `path` glob and optional `sheet` glob. Text tables
use sheet name `table`. `id_columns` maps source header to an explicit namespace;
use the **same namespace** for a key column and its differently named data column.
`person_columns` identifies canonical person/donor columns in key sheets.
`header_row`, `delimiter`, and `encoding` can override parsing. `materials`,
`data_types`, `material_column`/`material_values`, and `presence_columns` control
availability labeling. `ignore: true` excludes an intentionally irrelevant file.
`namespace_scope_columns` can associate an ID column with scope columns, for
example `{ "ID_ORIGINAL": ["Study"] }`. Rules with `enabled: false` are examples
only. Later matching rules override settings; `id_columns` mappings are combined.

The reader handles row-based ID tables. Transposed matrices with sample IDs in
column headers, headerless files, IDs stored only in filenames, and unsupported
binary formats need a suitable exported ID manifest/configuration; they are not
silently treated as covered. XLSX reading is streaming for worksheet rows, but
the reader may load the workbook's shared-string table into memory. XML fallback
limits each worksheet/shared-string XML component to 512 MiB and metadata XML to
16 MiB; hitting a limit is logged as incomplete reading. Use an ID-only
CSV/TSV export for a workbook too large for the job's memory.

## Locked files remain locked

Key-overview workbooks matching `Key files ID to ID/ID keys overview_projects*.xlsx`
are excluded before opening them, both in the catalogue and Llama preview reader. Other OLE/encrypted XLSX files are skipped after signature detection;
permission-denied files are logged and left alone. The scripts have no password
input or decryption support and never attempt to unlock files.

`skipped_files.jsonl` records catalogue skips. Readable keys, including
`20261006_gwas_array_df_big_ids.tsv`, are still used. Missing locked mappings can
leave some IDs unresolved; the catalogue retains usable results and exits 2 to
indicate coverage needs review. `all_observed_ids_resolved` is false when any file
is skipped. Source files are never modified.

## Documentation

- [openpyxl read-only worksheets](https://openpyxl.readthedocs.io/en/stable/optimized.html)
- [openpyxl loading options](https://openpyxl.readthedocs.io/en/stable/api/openpyxl.reader.excel.html)
