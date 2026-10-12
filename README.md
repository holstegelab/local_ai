# Local AI for ResearchDrive metadata

Scripts for read-only ResearchDrive inventories, person-level identifier catalogues,
and provisional specimen/assay metadata extraction with local Llama models.

## Get started

```bash
git clone https://github.com/holstegelab/local_ai.git
cd local_ai/llama_tools
```

Run on the Linux host or compute node where the research data is accessible.
Requirements include Python 3.10+, conda and bubblewrap with unprivileged user
namespaces. Automatic mounting also needs rclone, mountpoint and fusermount.
GPU inference needs a compatible NVIDIA GPU and driver. Installation and model
downloads need internet access; inference runs locally in an isolated worker.

See [setup and inventory instructions](llama_tools/README.md) before running setup.
The scripts keep their conda environment and model directory beside the scripts.
Existing Spider installations can reuse their environment; preserve local edits
when copying updated scripts. Replace example site paths and allocation options
with those appropriate for your execution host.

## Workflows

- [Inventory and bounded file review](llama_tools/README.md): list files and review
  supported table/text previews with local Llama.
- [Person catalogue](llama_tools/SAMPLE_CATALOG.md): link exact identifiers using
  authoritative mappings; no GPU required.
- [Specimen and assay extraction](llama_tools/FAIR_EXTRACTION.md): extract evidence
  and propose column meanings for human review.
- [Extraction task and rules](llama_tools/FAIR_EXTRACTION_TASK.md).
- [Model choice, download and attribution](llama_tools/FAIR_MODEL.md).

## Research data

This repository contains code, documentation, a prompt, synthetic tests and an
example configuration. Research data, catalogues, generated reports, private
source maps, credentials, environments and model weights are excluded. Keep those
in appropriately restricted storage. A private source map is optional; inspect
selection with `--plan-only` before extracting metadata.

Source data is read-only. Each run writes to a new output directory. Model
interpretations require review; they do not establish person identity, completed
assays, verified file availability or FAIR compliance.

## Lightweight checks

From `llama_tools/`:

```bash
python3 -m unittest discover -s . -p test_fair_extract.py -v
```

These synthetic checks need no ResearchDrive mount or model. GPU inference and
source access must be validated on the intended execution host. Model weights
have their own license terms; see the model documentation. No weights are included.
