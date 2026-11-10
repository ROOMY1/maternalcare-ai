"""Bottleneck analysis (report §5.2): latency per stage, tokens/second, VRAM, and the effect of top-k.

For each in-scope benchmark question (after one warm-up call):
    embed ms, search ms, LLM ms (from the pipeline timers), generated tokens and tokens/s
    (Ollama's eval_count / eval_duration in the response metadata), and VRAM in use.
Then the same with top-k = 2, 4, 8 to show how a longer context changes LLM time and page hit@k.

Usage:  python eval/latency_eval.py

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import statistics
import time
from typing import Any

from common import gold_pages, load_benchmark, save_results, vram_used_mb

from rag.llm import get_chat_model
from rag.prompts import build_messages
from rag.retriever import format_for_tool, get_retriever
from safety.arabic_text import detect_language


def med(xs: list[float]) -> float:
    return round(statistics.median(xs), 1) if xs else 0.0


def run(k: int, queries: list[dict[str, Any]], retriever: Any, llm: Any) -> dict[str, Any]:
    emb, srch, gen, toks, tps, hits = [], [], [], [], [], 0
    for q in queries:
        chunks = retriever.search(q["question"], k=k, threshold=0.0)
        emb.append(retriever.last_timing["embed"])
        srch.append(retriever.last_timing["search"])
        hits += any((c.source, c.page) in gold_pages(q) for c in chunks)
        msgs = build_messages(q["question"], format_for_tool(chunks), detect_language(q["question"]))
        t0 = time.perf_counter()
        out = llm.invoke(msgs)
        gen.append((time.perf_counter() - t0) * 1000)
        meta = getattr(out, "response_metadata", {}) or {}
        n, dur = meta.get("eval_count"), meta.get("eval_duration")  # tokens, nanoseconds
        if n and dur:
            toks.append(n)
            tps.append(n / (dur / 1e9))
    return {"k": k, "n": len(queries), "embed_ms": med(emb), "search_ms": med(srch),
            "llm_ms": med(gen), "total_ms": med([a + b + c for a, b, c in zip(emb, srch, gen)]),
            "generated_tokens": med(toks), "tokens_per_s": med(tps),
            "page_hit@k": round(hits / len(queries), 3)}


def main() -> int:
    queries = [q for q in load_benchmark() if q["type"] in ("factual", "medication")]
    retriever, llm = get_retriever(), get_chat_model()
    vram_idle = vram_used_mb()
    llm.invoke("Hello")  # warm-up (model load is excluded from the numbers)
    retriever.search("warm-up", k=1, threshold=0.0)
    rows = [run(k, queries, retriever, llm) for k in (2, 4, 8)]
    vram_loaded = vram_used_mb()

    md = ("| top-k | n | embed ms | search ms | LLM ms | total ms | generated tokens | tokens/s | page hit@k |\n"
          "|---|---|---|---|---|---|---|---|---|\n")
    for r in rows:
        md += (f"| {r['k']} | {r['n']} | {r['embed_ms']} | {r['search_ms']} | {r['llm_ms']} | "
               f"{r['total_ms']} | {r['generated_tokens']} | {r['tokens_per_s']} | {r['page_hit@k']} |\n")
    md += (f"\nMedians over the in-scope benchmark questions, after warm-up. VRAM in use: "
           f"{vram_idle} MB before loading the LLM in this script, {vram_loaded} MB with bge-m3 (fp16) "
           f"+ the LLM loaded (whole GPU, all processes).\n")
    print(md)
    print("Saved:", save_results("latency", {"rows": rows, "vram_idle_mb": vram_idle,
                                             "vram_loaded_mb": vram_loaded}, md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
