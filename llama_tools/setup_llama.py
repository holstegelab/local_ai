#!/usr/bin/env python3
"""Install a local CPU or NVIDIA CUDA Llama environment and verified GGUF model."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

REPO = "bartowski/Llama-3.2-3B-Instruct-GGUF"
MODEL = "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
CUDA_WHEELS = {"cu118": (11, 8), "cu121": (12, 1), "cu122": (12, 2),
               "cu123": (12, 3), "cu124": (12, 4), "cu125": (12, 5),
               "cu130": (13, 0), "cu132": (13, 2)}


def run(command):
    print("Running:", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def detect_cuda_wheel():
    smi = shutil.which("nvidia-smi")
    if not smi:
        raise RuntimeError("nvidia-smi is missing. Run setup in a GPU session, or select --cuda-wheel explicitly.")
    result = subprocess.run([smi], capture_output=True, text=True, timeout=30)
    match = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", result.stdout)
    if result.returncode or not match:
        raise RuntimeError("No usable NVIDIA GPU/driver was detected: " + result.stderr.strip())
    supported = tuple(map(int, match.groups()))
    # Older GPUs cannot use CUDA 13; query compute capability when the driver supports it.
    result = subprocess.run([smi, "--query-gpu=compute_cap", "--format=csv,noheader"],
                            capture_output=True, text=True, timeout=30)
    capabilities = []
    if result.returncode == 0:
        capabilities = [float(line.strip()) for line in result.stdout.splitlines() if re.fullmatch(r"\d+\.\d+", line.strip())]
    candidates = [name for name, version in CUDA_WHEELS.items() if version <= supported
                  and (version[0] < 13 or capabilities and min(capabilities) >= 7.5)
                  and (version[0] >= 13 or not capabilities or max(capabilities) < 10.0)
                  and (version[0] >= 12 or not capabilities or max(capabilities) <= 8.9)]
    if not candidates:
        raise RuntimeError("No known wheel fits this driver/GPU. Load a compatible CUDA toolkit and use --cuda-wheel source.")
    selected = max(candidates, key=CUDA_WHEELS.get)
    print(f"Driver supports CUDA {supported[0]}.{supported[1]}; selecting {selected}.", flush=True)
    return selected


def install_package(conda, environment, python, backend, wheel):
    # Download from exactly the selected backend index, avoiding an accidental CPU wheel.
    if wheel != "source":
        with tempfile.TemporaryDirectory(prefix="llama-wheel-", dir=environment.parent) as directory:
            command = [str(python), "-m", "pip", "download", "--no-deps", "--only-binary=:all:",
                       "--index-url", f"https://abetlen.github.io/llama-cpp-python/whl/{wheel}",
                       "--dest", directory, "llama-cpp-python"]
            result = subprocess.run(command)
            if result.returncode == 0:
                package = next(Path(directory).glob("*.whl"))
                # Replace a previous CPU build when switching to CUDA.
                run([python, "-m", "pip", "install", "--upgrade", "--force-reinstall", package])
                return
            if backend == "cuda":
                raise RuntimeError(f"No usable {wheel} wheel was downloaded. To build locally, load a CUDA toolkit "
                                   "(nvcc must be available), then rerun with --backend cuda --cuda-wheel source.")
    print("Building llama-cpp-python in the conda environment.", flush=True)
    if backend == "cuda" and not shutil.which("nvcc"):
        raise RuntimeError("CUDA source builds require nvcc. Load your Spider CUDA toolkit before rerunning.")
    run([conda, "install", "--yes", "--prefix", environment,
         "--override-channels", "--channel", "conda-forge", "cmake", "ninja", "cxx-compiler"])
    build_env = os.environ.copy()
    build_env["CMAKE_ARGS"] = f"-DGGML_CUDA={'ON' if backend == 'cuda' else 'OFF'} -DGGML_NATIVE=OFF"
    subprocess.run([conda, "run", "--no-capture-output", "--prefix", str(environment),
                    "python", "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-cache-dir",
                    "--no-binary=llama-cpp-python", "llama-cpp-python"], env=build_env, check=True)


def install_cuda_runtime(python, wheel):
    """Install only the runtime and BLAS libraries needed by prebuilt CUDA wheels."""
    if wheel == "source":
        # A source build uses the existing, explicitly loaded CUDA toolkit.
        return
    major, minor = CUDA_WHEELS[wheel]
    if major == 13:
        # CUDA toolkit and cuBLAS component versions are not always the same.
        # NVIDIA's CUDA 13.2 component table lists cuBLAS 13.3.
        cublas_minor = {0: 0, 2: 3}[minor]
        packages = [f"nvidia-cuda-runtime==13.{minor}.*", f"nvidia-cublas==13.{cublas_minor}.*"]
    else:
        cublas_minor = 11 if major == 11 else minor
        packages = [f"nvidia-cuda-runtime-cu{major}=={major}.{minor}.*",
                    f"nvidia-cublas-cu{major}=={major}.{cublas_minor}.*"]
    # Restrict versions to the chosen toolkit, rather than pulling a newer CUDA release.
    run([python, "-m", "pip", "install", "--only-binary=:all:", *packages])


def check_installation(python, model, backend):
    print("\nChecking local inference and the read-only sandbox...")
    gpu_layers = "-1" if backend == "cuda" else "0"
    script = Path(__file__).with_name("rd_inventory.py")
    run([python, script, "--check", "--model", model, "--gpu-layers", gpu_layers])
    print("\nSetup complete. If ResearchDrive is mounted on this node, run:")
    print(f"{python} {script} --root /home/holstegelab-gozhegov/rd-mount --gpu-layers {gpu_layers} --review-files")
    if backend == "cuda":
        print("Or mount ResearchDrive inside your GPU job with: bash run_rd_llama.sh")


def download_model(destination, repo, filename, revision):
    url = "https://huggingface.co/api/models/" + repo
    if revision != "main":
        url += "/revision/" + urllib.parse.quote(revision, safe="")
    with urllib.request.urlopen(url + "?blobs=true", timeout=120) as response:
        metadata = json.load(response)
    entry = next((x for x in metadata["siblings"] if x["rfilename"] == filename), None)
    if not entry or "lfs" not in entry:
        raise RuntimeError("Model not found, or no published LFS SHA256 is available.")
    expected = entry["lfs"]["sha256"]
    size = entry["lfs"]["size"]
    commit = metadata["sha"]
    if destination.exists():
        if destination.stat().st_size != size or sha256(destination) != expected:
            raise RuntimeError(f"Existing model has a different checksum: {destination}. Use another --base.")
        print("Existing model verified.")
    else:
        if shutil.disk_usage(destination.parent).free < size + 256 * 1024 * 1024:
            raise RuntimeError("Not enough free disk space for the model.")
        url = (f"https://huggingface.co/{repo}/resolve/{commit}/"
               + urllib.parse.quote(filename, safe="/") + "?download=true")
        partial = destination.with_name(destination.name + f".{os.getpid()}.partial")
        downloaded = 0
        try:
            with urllib.request.urlopen(url, timeout=120) as response, partial.open("xb") as output:
                while block := response.read(8 * 1024 * 1024):
                    output.write(block)
                    downloaded += len(block)
                    print(f"\rModel: {downloaded / 1e9:.2f} / {size / 1e9:.2f} GB", end="", flush=True)
            print()
            if downloaded != size or sha256(partial) != expected:
                raise RuntimeError("Download size/checksum mismatch; model was not installed.")
            if destination.exists():
                raise RuntimeError("Model destination appeared during download; refusing to replace it.")
            partial.rename(destination)
        finally:
            if partial.exists():
                partial.unlink()
    return {"repository": repo, "filename": filename, "revision": commit,
            "sha256": expected, "bytes": size, "model_path": str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--conda", default=os.environ.get("CONDA_EXE", "conda"))
    parser.add_argument("--model-repo", default=REPO)
    parser.add_argument("--model-file", default=MODEL)
    parser.add_argument("--revision", default="main")
    parser.add_argument("--download-model-only", action="store_true",
                        help="Download/verify the selected GGUF and record its provenance; reuse the existing environment")
    parser.add_argument("--backend", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--cuda-wheel", choices=("auto", "source", *CUDA_WHEELS), default="auto",
                        help="CUDA wheel index; auto detects driver/GPU, source builds using an existing nvcc toolkit")
    parser.add_argument("--repair-runtime", action="store_true",
                        help="Install missing CUDA runtime libraries and rerun checks; reuse existing Llama package/model")
    args = parser.parse_args()
    if Path(args.model_file).name != args.model_file:
        parser.error("--model-file must be a filename, without directories")
    if args.download_model_only:
        if args.repair_runtime:
            parser.error("--download-model-only cannot be combined with --repair-runtime")
        base = args.base.expanduser().resolve()
        models = base / "models"
        models.mkdir(parents=True, exist_ok=True)
        manifest = download_model(models / args.model_file, args.model_repo, args.model_file, args.revision)
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        manifest["downloaded_utc"] = stamp
        manifest["mode"] = "download-model-only"
        with (base / f"model-download-{stamp}.json").open("x") as handle:
            json.dump(manifest, handle, indent=2)
        print(f"Model verified: {models / args.model_file}")
        return
    if args.repair_runtime and (args.backend != "cuda" or args.cuda_wheel == "source"):
        parser.error("--repair-runtime requires --backend cuda and a prebuilt CUDA wheel selection")
    conda = shutil.which(args.conda)
    if not conda:
        parser.error("Conda is not available. Load/initialize your Spider conda installation first.")
    # Fail before downloading gigabytes if Spider cannot enforce read-only access.
    run([sys.executable, Path(__file__).with_name("rd_inventory.py"), "--check", "--metadata-only"])
    wheel = "cpu" if args.backend == "cpu" else (detect_cuda_wheel() if args.cuda_wheel == "auto" else args.cuda_wheel)
    base = args.base.expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    environment = base / "conda-env"
    python = environment / "bin/python"
    model = base / "models" / args.model_file
    if args.repair_runtime:
        if not python.is_file() or not model.is_file():
            parser.error("Repair requires an existing conda-env and model. Run normal setup first.")
        install_cuda_runtime(python, wheel)
        check_installation(python, model, args.backend)
        return
    if not python.exists():
        run([conda, "create", "--yes", "--prefix", environment,
             "--override-channels", "--channel", "conda-forge", "python=3.11", "pip"])
    install_package(conda, environment, python, args.backend, wheel)
    if args.backend == "cuda":
        install_cuda_runtime(python, wheel)
    models = base / "models"
    models.mkdir(exist_ok=True)
    manifest = download_model(models / args.model_file, args.model_repo, args.model_file, args.revision)
    # An immutable setup record is kept for each run, including the exact package versions.
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    manifest["installed_utc"] = stamp
    manifest["python"] = str(python)
    manifest["backend"] = args.backend
    manifest["wheel_index"] = wheel
    manifest["packages"] = subprocess.check_output(
        [str(python), "-m", "pip", "freeze"], text=True).splitlines()
    with (base / f"setup-{stamp}.json").open("x") as handle:
        json.dump(manifest, handle, indent=2)
    check_installation(python, model, args.backend)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Setup failed: {exc}")
