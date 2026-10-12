# Weights for the brain/blood/genetics metadata task

Use **Llama 3.1 8B Instruct, Q5_K_M GGUF** for the first reviewed pilot.
It fits the existing `llama-cpp-python` environment and an allocated A100 comfortably.
This is a practical starting choice, not a claim that it has been validated on the lab's metadata.

| Setting | Choice |
|---|---|
| Original model | `meta-llama/Llama-3.1-8B-Instruct` |
| Quantization publisher | `bartowski/Meta-Llama-3.1-8B-Instruct-GGUF` |
| File | `Meta-Llama-3.1-8B-Instruct-Q5_K_M.gguf` |
| Pinned repository revision | `bf5b95e96dac0462e2a09145ec66cae9a3f12067` |
| Download size | Approximately 5.73 GB, decimal units |
| Initial context | 8,192 tokens, including input and output |
| Sampling | Temperature 0, fixed seed |
| GPU layers | `-1` inside an allocated GPU session |

The filename and size are listed by the [quantization publisher](https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/tree/main).
The revision was checked against the [publisher API](https://huggingface.co/api/models/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF?blobs=true) on 12 October 2026.
The original model supports a larger context, but bounded table samples are easier to
review and use less memory. See the [Meta model card](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct).

The existing Llama 3.2 3B Q4_K_M can still run a quick smoke test. Prefer the 8B model
for the initial metadata interpretation pilot: complex headers, status fields and
abbreviations deserve more capacity. Whether it improves this task must be checked
against a small manually reviewed set of source tables. More parameters cannot
recover undocumented brain regions or prove that a sequencing run completed.

For a smaller GPU, use `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` from the same
repository (approximately 4.92 GB). Q5_K_M keeps more precision for a modest size
increase. Plan roughly **10–12 GiB free GPU memory** and **16–24 GiB host RAM** for
the Q5 pilot at 8k context. These are planning estimates, not measured requirements;
context, runtime version, batching and GPU offload change memory use. CPU execution
is possible with zero GPU layers but slower. The existing 12 GB host-memory example
in the inventory README was for the smaller 3B model.

Consider a 70B model only after recording specific 8B interpretation failures on
reviewed examples and checking the available GPU memory. First improve the prompt,
explicit terminology mappings and supplied documentation. Do not use a larger
model to guess specimen identities or fill missing information.

## Download on Spider without reinstalling the working environment

Run this from the directory containing `setup_llama.py`. This downloads only
weights and writes a new provenance record, leaving the working conda/CUDA
installation alone. The downloader verifies the pinned revision's published size
and SHA256; an existing mismatching file is rejected. Outbound HTTPS is needed
for downloading, but inference is local. No download has been performed by this change.

```bash
cd /project/holstegelab/Share/llama
python3 setup_llama.py --download-model-only \
  --model-repo bartowski/Meta-Llama-3.1-8B-Instruct-GGUF \
  --model-file Meta-Llama-3.1-8B-Instruct-Q5_K_M.gguf \
  --revision bf5b95e96dac0462e2a09145ec66cae9a3f12067
```

Pass this GGUF path explicitly to the metadata script's `--model` option. Keep the
download manifest with the output run metadata, along with the model checksum,
context, prompt version and runtime version. A fixed seed reduces sampling
variation but does not guarantee identical output across hardware or runtime changes.

Use the model to propose column meanings and terminology mappings. Validate the
response structure, column references and quoted evidence in Python. Compute person
links, distinct specimen/region counts and availability summaries from source records,
never from generated prose. Keep every interpretation labeled for review.

## Model attribution

Built with Llama. Meta provides the original weights; bartowski provides this
third-party GGUF quantization. It is governed by the
[Llama 3.1 Community License](https://github.com/meta-llama/llama-models/blob/main/models/llama3_1/LICENSE)
and associated acceptable-use terms. Quantization does not replace those terms.
This document distributes no model weights.
