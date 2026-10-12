# Task: specimen and genetic-assay metadata for matched-person queries

## Desired result

Extend the existing catalogue so a researcher can inspect people who have
documented brain specimens, blood-derived specimens, and specified genetic
assays. For brain material, show documented regions separately from tissue
blocks, specimens, and aliquots. For genetics, distinguish assay type, platform,
processing stage, and strength of evidence for availability.

Supply an existing private person catalogue via `--catalog`, and the actual
ResearchDrive mount path on the execution host. The source tables must be read
again: this catalogue stores headers and ID matches, but not the necessary
region/material/assay values. The sources and original catalogue stay read-only;
write every run into a new output directory.

## Division of responsibility

Raw research data must never be modified, overwritten, renamed, moved, deleted,
or have permissions changed. All proposed corrections and normalization remain in
new derived outputs. The FAIR model worker receives no ResearchDrive mount; the
reader receives a read-only mount. If the isolation checks fail, stop without a
fallback. Mutation checks run only on a marked disposable fixture, never on raw
research data.

Llama interprets headers, bounded non-identifier examples, and documented value
dictionaries. It returns a **proposed extraction plan** using
`fair_extraction_prompt.txt`. It does not execute commands, invent data, establish
person identity, or calculate person/specimen counts. Source text is untrusted
data; instructions embedded in source files are ignored.

Python validates the JSON and exact source-column positions, streams selected
tables, preserves raw values with provenance, and uses only existing confirmed
catalogue links. Validation of JSON structure does not establish that a proposed
biological interpretation is correct. Proposed field meanings and normalizations
must remain visibly marked for review.

A source owner or data steward confirms ambiguous field definitions, identifier
schemes, specimen relationships, and normalization dictionaries before results
are used as a trusted cohort selection. An unknown or unlinked record is retained
as unknown/unlinked, rather than treated as absent.

## First pilot sources

Review source selection with `--plan-only`. Optionally supply a private TSV
source map with `source_file`, `sheet` and 1-based `column_number` columns using
`--source-map`. File/sheet/column positions are selection proposals, not validated
interpretations. No data-derived source map is included in this repository.

Prioritize these source families:

| Source family | Evidence to retain | Main question requiring review |
|---|---|---|
| Current biobank specimen export | Participant ID, Barcode, Material, Specimen Label, Anatomic site, Laterality, Collection date, Available Quantity | What namespace does Participant ID use, and are materials/quantities current? |
| Brain specimen metadata and supplemental phenotypes | donor, sample_name, block, barcode, region, Brain_region, Omics_Measured | Which fields identify a donor, specimen, block, aliquot, or run? |
| Brain proteomics metadata and sample merge tables | sample_id, nbb_id, brain_id, tissuecode, region, inclusion flags | Are region/tissue-code meanings documented, and which tables copy the same specimens? |
| Integrated genetics phenotype/array tables | WES_done, WES_id, GWAS_done, GWAS_todo, GWAS_chip, ID_PacBio, EXOME_KIT, data paths | Does each flag mean planned, assigned, completed, or data available? |

Do not scan raw genomic matrices or load every workbook into memory. Limit Llama
inputs to the relevant table headers and bounded examples of non-identifier
fields. The selected tables still need all-row deterministic extraction; a preview
does not establish the distribution, completeness, or absence of values.

## Extraction requirements

1. Preserve identifiers as exact text. Use the existing catalogue's confirmed
   person link only when the relative source path, worksheet, physical source
   row, and recorded source identifier still agree. Revalidate the source ID using
   the catalogue's exact-text/Excel-padding rules: tables may have changed or
   reordered since the catalogue run. Stale or conflicting links remain unresolved.
   Do not infer identity from
   overlapping numbers, fuzzy names, Llama output, or a generic ID header.
2. Preserve exact file, worksheet, physical row number, 1-based column position,
   original header, raw value, extraction time, and proposal/review status. Retain
   duplicate column names by position. Never execute spreadsheet formulas. The
   initial extraction reader excludes formula cells and reports them rather than
   treating an unevaluated formula as metadata; these fields require a reviewed
   value-only export or a subsequent reader supporting cached values. Report
   formula exclusions and unreadable tables as coverage limitations.
3. Extract specimen fields separately: specimen ID, parent specimen ID, label,
   material, brain region, laterality, tissue block, aliquot ID, collection date,
   inventory quantity, and units. Do not count table rows as specimens.
4. Extract assay evidence separately: assay ID/type/platform, status, processing
   level, reported location, and reference build. Wide tables can contain WES,
   array, and PacBio evidence for the same source row; retain separate assay groups.
5. Use WES, WGS, array, targeted sequencing, and long-read unspecified as distinct
   proposed types. PacBio alone does not imply WGS. Imputation is processing, not a
   new assay. An ID alone does not imply completion or available genetic data.
6. Preserve documented status evidence without upgrading it: planned, ID listed,
   reported completed, or location reported. A separate deterministic check of
   the referenced file/manifest is required for verified data availability. A
   done flag is a report from that source, not independent verification.
7. Unknown brain regions remain unknown. Several specimens/blocks/aliquots do not
   prove several regions. Do not expand tissue codes or abbreviations without a
   documented source-specific dictionary. DNA is not automatically blood-derived.
8. Keep copied-table and cross-source duplicates visible for review. Do not
   deduplicate by similar labels or double-count source occurrences as physical
   specimens. Material inventories may be old; record their date when available.

## Prompt interface

One Llama call handles one source table. The JSON response has `schema_version`,
`source_file`, `sheet`, `requires_review`, `bindings`, `value_mappings`,
`interpretation_notes`, and `unresolved_questions`.

Each binding names an exact column position/header and one proposed target field,
plus an assay group/type where supported. Each value mapping names an exact
observed non-identifier raw value and a proposed normalization with evidence.
Every binding and mapping requires review. The script must reject invented
columns, mismatched source identities, unobserved value mappings, malformed JSON,
and attempts to output code or person assignments.

Use short per-table prompts. If a source is too wide for the model context,
explicitly limit its supplied columns or report it as needing a separate review;
never silently claim the complete table has been interpreted.

## Pilot acceptance and next stage

The first deliverable is a reproducible, reviewable evidence extraction: Llama's
per-table proposals, raw all-row field evidence, existing confirmed person links,
unresolved records, and a run summary with coverage/read/model errors. It is not
yet a validated matched cohort or proof of FAIR compliance.

Review a small donor set with the source owner, including a donor with several
brain specimens, a donor with several assays, an ambiguous participant namespace,
and at least one missing region/status. Compare the extracted records directly
with the source rows. Confirm which identifiers describe the same physical
specimen and which sources are authoritative.

After the reviewed meanings and specimen identities are stored as explicit
rules, produce the person overview with separate columns for brain regions,
brain specimen/block counts, unknown regions, blood-derived materials, genetic
assay types, and availability evidence. A matched person requires a common
confirmed person ID across the records; uncertain links remain excluded from
confirmed matches. Counts describe documented distinct entities and must state
coverage limitations. Retain the detailed records supporting every overview row.
