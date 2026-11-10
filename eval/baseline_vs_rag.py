"""Standard LLM vs RAG on the 5 medication questions of the benchmark (report §1.3 case study).

Baseline: the SAME local model (qwen2.5:7b-instruct), asked the question directly, with no
retrieved context and no safety layers. RAG: rag_pipeline.answer_question() (retrieval, grounded
prompt, dosage filter, citations). Latency = median of 3 runs after one warm-up call.

The table marks automatically: whether a citation is present and whether a dose expression appears
(safety.dosage_filter.find_doses). Whether an answer contains a hallucination must be judged by a
person against the cited page -> column "hallucination (manual)" is left for the reviewer.

Usage:  python eval/baseline_vs_rag.py [--runs 3]

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import argparse
import statistics
import time
from typing import Any

from common import load_benchmark, save_results, strip_disclaimer

from rag.llm import get_chat_model
from rag.rag_pipeline import answer_question
from rag.retriever import get_retriever
from safety.dosage_filter import find_doses


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    questions = [q for q in load_benchmark() if q["type"] == "medication"][:5]
    llm = get_chat_model()
    retriever = get_retriever()
    llm.invoke("Hello")  # warm-up: loads the model into VRAM; not measured
    answer_question("warm-up question about pregnancy", retriever=retriever, llm=llm)

    rows: list[dict[str, Any]] = []
    for q in questions:
        base_times, rag_times = [], []
        base_text, rag = "", None
        for _ in range(args.runs):
            t0 = time.perf_counter()
            base_text = str(llm.invoke(q["question"]).content)
            base_times.append((time.perf_counter() - t0) * 1000)
            rag = answer_question(q["question"], retriever=retriever, llm=llm)
            rag_times.append(rag.latency_ms.total)
        assert rag is not None
        rag_text = strip_disclaimer(rag.answer)
        rows.append({
            "id": q["id"], "question": q["question"], "language": q["language"],
            "gold": f"{q['source']} p.{q['page']}",
            "baseline_answer": base_text, "baseline_latency_ms": round(statistics.median(base_times)),
            "baseline_dose_given": bool(find_doses(base_text)),
            "rag_answer": rag_text, "rag_latency_ms": round(statistics.median(rag_times)),
            "rag_citations": [f"{c.source} p.{c.page}" for c in rag.citations],
            "rag_cited": rag.cited, "rag_not_found": rag.not_found,
            "rag_dose_given": bool(find_doses(rag_text)), "rag_dosage_filtered": rag.dosage_filtered,
            "hallucination_manual": "",
        })
        print(f"{q['id']}: baseline {rows[-1]['baseline_latency_ms']} ms | RAG {rows[-1]['rag_latency_ms']} ms")

    md = ("| id | Question | Citation (baseline / RAG) | Dose given (baseline / RAG) | "
          "Median latency ms (baseline / RAG) | Hallucination (manual) |\n|---|---|---|---|---|---|\n")
    for r in rows:
        md += (f"| {r['id']} | {r['question']} | no / {'yes' if r['rag_citations'] else 'no'} | "
               f"{'yes' if r['baseline_dose_given'] else 'no'} / {'yes' if r['rag_dose_given'] else 'no'} | "
               f"{r['baseline_latency_ms']} / {r['rag_latency_ms']} | to review |\n")
    md += "\n### Answers side by side\n"
    for r in rows:
        md += (f"\n**{r['id']} — {r['question']}** (gold: {r['gold']})\n\n"
               f"*Baseline (no retrieval):* {r['baseline_answer']}\n\n"
               f"*RAG:* {r['rag_answer']}\n\n*RAG sources:* {', '.join(r['rag_citations']) or 'none'}\n")
    print(md)
    print("Saved:", save_results("baseline_vs_rag", {"runs": args.runs, "rows": rows}, md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
