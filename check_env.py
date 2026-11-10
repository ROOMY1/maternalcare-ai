"""Environment smoke test for MaternalCare AI (milestone M0).

Purpose
    One command that tells a teammate whether their laptop is ready:
    Python version, packages, hardware tier, Ollama + model, a tool-calling test (needed in
    Phase 2), the embedding model (dimension + max length) and a Chroma cosine collection.

Usage:  python check_env.py            (all checks)
        python check_env.py --quick    (skip loading the embedding model)

Exit code 0 = all required checks passed.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import argparse
import importlib
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass

RESULTS: list[tuple[str, bool, str]] = []
PACKAGES = ["chromadb", "sentence_transformers", "torch", "langchain_core", "langchain_community",
            "langchain_text_splitters", "langchain_ollama", "pymupdf", "pypdf", "pydantic",
            "pydantic_settings", "streamlit", "ragas", "pandas", "dotenv", "pytest"]


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def hardware() -> str:
    """Detect GPU/VRAM/RAM and return the recommended tier."""
    ram_gb = 0.0
    try:
        import psutil  # optional; installed with some packages

        ram_gb = psutil.virtual_memory().total / 1024**3
    except ImportError:
        pass
    vram_gb, gpu = 0.0, "none"
    try:
        import torch

        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
            vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            gpu = "Apple mps"
    except ImportError:
        pass
    if vram_gb >= 12:
        tier = "qwen2.5:14b-instruct, embeddings on cuda"
    elif vram_gb >= 7.5:
        tier = "qwen2.5:7b-instruct, embeddings on cuda (default tier)"
    elif vram_gb >= 4 or gpu == "Apple mps":
        tier = "qwen2.5:7b-instruct (partial offload) or qwen2.5:3b-instruct, embeddings on cpu/mps"
    else:
        tier = "qwen2.5:3b-instruct, embeddings on cpu"
    check("Hardware", True, f"{platform.processor() or platform.machine()} | GPU {gpu} "
          f"{vram_gb:.1f} GB VRAM | RAM {ram_gb:.0f} GB | recommended: {tier}")
    return tier


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip the embedding-model load")
    args = ap.parse_args()
    print("MaternalCare AI — environment check\n")

    v = sys.version_info
    check("Python 3.11", (v.major, v.minor) == (3, 11), platform.python_version())

    missing = []
    for pkg in PACKAGES:
        try:
            importlib.import_module(pkg)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{pkg} ({exc.__class__.__name__})")
    check("Packages import", not missing, (", ".join(missing) + ". Fix: pip install -r requirements.txt")
          if missing else f"{len(PACKAGES)} packages OK")
    if missing:
        return summary()

    from config import BASE_DIR, get_settings

    s = get_settings()
    env_ok = (BASE_DIR / ".env").exists()
    check(".env present", env_ok, "" if env_ok else "Fix: copy .env.example .env")
    hardware()

    # --- Ollama
    from rag.llm import OllamaUnavailableError, check_ollama, get_chat_model

    import os

    exe = shutil.which("ollama")
    default_exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    if not exe and default_exe.exists():  # PATH not refreshed in shells opened before the install
        exe = str(default_exe)
    ver = ""
    if exe:
        ver = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip()
    try:
        models = check_ollama()
        check("Ollama reachable + model pulled", True, f"{s.llm_model} | {ver or 'version n/a'} | "
              f"models: {', '.join(models)}")
        ollama_ok = True
    except OllamaUnavailableError as exc:
        check("Ollama reachable + model pulled", False, str(exc))
        ollama_ok = False

    if ollama_ok:
        from langchain_core.tools import tool

        @tool
        def get_week_from_edd(edd: str) -> int:
            """Return the gestational week for an estimated due date given as YYYY-MM-DD."""
            return 28

        try:
            t0 = time.perf_counter()
            llm = get_chat_model().bind_tools([get_week_from_edd])
            msg = llm.invoke("My due date is 2027-02-01. Use the tool to tell me my pregnancy week.")
            calls = getattr(msg, "tool_calls", []) or []
            check("Tool-calling smoke test", bool(calls) and calls[0]["name"] == "get_week_from_edd",
                  f"tool_calls={[c['name'] for c in calls]} args={[c['args'] for c in calls]} "
                  f"({time.perf_counter() - t0:.1f} s incl. warm-up)")
        except Exception as exc:  # noqa: BLE001
            check("Tool-calling smoke test", False, repr(exc))

    # --- Embeddings
    if not args.quick:
        try:
            from rag.embeddings import Embedder

            t0 = time.perf_counter()
            emb = Embedder()
            vec = emb.embed_query("هل الباراسيتامول مسموح أثناء الحمل؟")
            check("Embedding model", len(vec) == emb.dimension,
                  f"{emb.model_name} on {emb.device} | dim {emb.dimension} | max_seq_length "
                  f"{emb.max_seq_length} | chunk {s.chunk_size_tokens} tokens | "
                  f"{time.perf_counter() - t0:.1f} s")
        except Exception as exc:  # noqa: BLE001
            check("Embedding model", False, f"{exc!r}. Fix: check internet for the first download")

    # --- Chroma cosine
    try:
        from rag.index_builder import collection_space, create_collection, get_client

        # Chroma keeps its SQLite file open on Windows, so ignore cleanup errors of the temp dir.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client = get_client(Path(tmp) / "vdb")
            col = create_collection(client, "smoke", "test-model", 400, 60)
            col.upsert(ids=["a", "b"], embeddings=[[1.0, 0.0], [0.0, 1.0]], documents=["a", "b"])
            res = col.query(query_embeddings=[[1.0, 0.0]], n_results=2)
            space = collection_space(client.get_collection("smoke"))
            d = res["distances"][0]
            ok = space == "cosine" and abs(d[0]) < 1e-6 and abs(d[1] - 1.0) < 1e-6
            client.delete_collection("smoke")
            del client, col
        import chromadb

        check("Chroma cosine collection", ok, f"chromadb {chromadb.__version__} | space={space} | "
              f"distances {[round(x, 4) for x in d]} (expected [0, 1])")
    except Exception as exc:  # noqa: BLE001
        check("Chroma cosine collection", False, repr(exc))

    return summary()


def summary() -> int:
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + ("ALL CHECKS PASSED" if not failed else f"FAILED: {', '.join(failed)}"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
