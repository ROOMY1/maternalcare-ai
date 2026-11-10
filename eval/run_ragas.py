"""Answer the benchmark with the full pipeline, compute system safety numbers, and score the
answers with Ragas (Faithfulness, Answer Relevancy, Context Recall) using a LOCAL judge.

Judge: JUDGE_MODEL through Ollama (default qwen2.5:7b-instruct, the same family as the generator,
which is a known bias: a model judging its own family can be lenient). Embeddings for Answer
Relevancy: local bge-m3. Nothing goes to the cloud.

Rows Ragas cannot score (judge output not parsable -> NaN) are logged and reported as coverage.
Out-of-scope rows and not-found answers have no context, so they are not scored by Ragas; they are
counted in the safety table instead (not-found rate, doses given).

Also writes eval/results/answers_for_review_<time>.csv: every answer with empty rating columns.
The human review itself lives in eval/manual_review.csv (10 selected rows with the source sentence),
which this script never overwrites.

Usage:  python eval/run_ragas.py [--limit N]

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from typing import Any

from common import ROOT, load_benchmark, save_results, strip_disclaimer

from config import get_settings
from rag.llm import get_chat_model
from rag.rag_pipeline import answer_question
from rag.retriever import get_retriever
from safety.dosage_filter import find_doses


def generate_answers(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    retriever, llm = get_retriever(), get_chat_model()
    llm.invoke("Hello")  # warm-up
    out = []
    for q in queries:
        r = answer_question(q["question"], retriever=retriever, llm=llm)
        ans = strip_disclaimer(r.answer)
        out.append({**q, "answer": ans, "contexts": [c.text for c in r.retrieved],
                    "citations": [f"{c.source} p.{c.page}" for c in r.citations], "cited": r.cited,
                    "not_found": r.not_found, "dosage_filtered": r.dosage_filtered,
                    "dose_in_answer": bool(find_doses(ans)), "latency_ms": r.latency_ms.model_dump()})
        print(f"{q['id']} not_found={r.not_found} filtered={r.dosage_filtered} "
              f"llm={r.latency_ms.llm:.0f}ms :: {ans[:90]!r}")
    return out


def ragas_scores(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Score rows with Ragas 0.4.3 (legacy metric classes + LangChain wrappers, checked on install)."""
    from langchain_huggingface import HuggingFaceEmbeddings
    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics._answer_relevance import AnswerRelevancy
    from ragas.metrics._context_recall import LLMContextRecall
    from ragas.metrics._faithfulness import Faithfulness

    s = get_settings()
    judge = LangchainLLMWrapper(get_chat_model(model=s.judge_model))
    emb = LangchainEmbeddingsWrapper(HuggingFaceEmbeddings(
        model_name=s.embed_model, encode_kwargs={"normalize_embeddings": True}))
    ds = EvaluationDataset.from_list([
        {"user_input": r["question"], "response": r["answer"], "retrieved_contexts": r["contexts"],
         "reference": r["ground_truth"]} for r in rows])
    result = evaluate(ds, metrics=[Faithfulness(), AnswerRelevancy(strictness=1), LLMContextRecall()],
                      llm=judge, embeddings=emb,
                      run_config=RunConfig(timeout=600, max_workers=1, max_retries=2))
    df = result.to_pandas()
    scored, failed = [], []
    for r, (_, d) in zip(rows, df.iterrows()):
        vals = {m: (None if d.get(m) is None or (isinstance(d.get(m), float) and math.isnan(d.get(m)))
                    else round(float(d.get(m)), 3))
                for m in ("faithfulness", "answer_relevancy", "context_recall")}
        if any(v is None for v in vals.values()):
            failed.append(f"{r['id']}: {[k for k, v in vals.items() if v is None]}")
        scored.append({"id": r["id"], "language": r["language"], **vals})
    return scored, failed


def write_manual_review(rows: list[dict[str, Any]]) -> None:
    """Write all answers to eval/results/answers_for_review_<time>.csv.

    It never touches eval/manual_review.csv, which holds the human ratings and must not be
    overwritten by a new run.
    """
    from datetime import datetime

    path = ROOT / "eval" / "results" / f"answers_for_review_{datetime.now():%Y%m%d_%H%M%S}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: Excel shows Arabic
        w = csv.writer(f)
        w.writerow(["id", "language", "type", "question", "answer", "sources", "gold_source_page",
                    "rating (correct/partially correct/wrong/unsafe)", "faithful_to_sources (yes/no)",
                    "reviewer", "notes"])
        for r in rows:
            if r["type"] == "out_of_scope":
                continue
            w.writerow([r["id"], r["language"], r["type"], r["question"], r["answer"],
                        "; ".join(r["citations"]), f"{r['source']} p.{r['page']}", "", "", "", ""])


def mean(vals: list[float | None]) -> float | None:
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 3) if v else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only the first N questions (smoke test)")
    args = ap.parse_args()
    queries = load_benchmark()[: args.limit]
    t0 = time.perf_counter()
    rows = generate_answers(queries)
    write_manual_review(rows)

    oos = [r for r in rows if r["type"] == "out_of_scope"]
    dose = [r for r in rows if r["type"] == "dose_seeking"]
    inscope = [r for r in rows if r["type"] in ("factual", "medication")]
    safety = {
        "out_of_scope_not_found": f"{sum(r['not_found'] for r in oos)}/{len(oos)}",
        "dose_seeking_answers_with_a_dose": f"{sum(r['dose_in_answer'] for r in dose)}/{len(dose)}",
        "dose_seeking_filter_triggered": f"{sum(r['dosage_filtered'] for r in dose)}/{len(dose)}",
        "in_scope_wrongly_not_found": f"{sum(r['not_found'] for r in inscope)}/{len(inscope)}",
        "in_scope_with_citation_markers": f"{sum(r['cited'] for r in inscope)}/{len(inscope)}",
    }
    to_score = [r for r in inscope + dose if not r["not_found"] and r["contexts"]]
    scored, failed = ragas_scores(to_score) if to_score else ([], [])
    minutes = round((time.perf_counter() - t0) / 60, 1)

    def agg(lang: str | None) -> dict[str, Any]:
        sub = [x for x in scored if lang is None or x["language"] == lang]
        return {"n": len(sub), **{m: mean([x[m] for x in sub])
                                  for m in ("faithfulness", "answer_relevancy", "context_recall")}}

    summary = {"all": agg(None), "en": agg("en"), "ar": agg("ar")}
    md = ("### Ragas (local judge: " + get_settings().judge_model + ", embeddings bge-m3)\n\n"
          "| Subset | n scored | Faithfulness | Answer relevancy | Context recall |\n|---|---|---|---|---|\n")
    for k, v in summary.items():
        md += f"| {k} | {v['n']} | {v['faithfulness']} | {v['answer_relevancy']} | {v['context_recall']} |\n"
    md += (f"\nCoverage: {len(scored) - len(failed)}/{len(scored)} rows fully scored; "
           f"failed/partial: {failed or 'none'}. Not scored: out-of-scope and not-found rows "
           f"(no context). Run time {minutes} min.\n\n### System safety numbers\n\n| Check | Result |\n|---|---|\n")
    for k, v in safety.items():
        md += f"| {k.replace('_', ' ')} | {v} |\n"
    md += "\n| id | lang | Faithfulness | Answer relevancy | Context recall |\n|---|---|---|---|---|\n"
    for x in scored:
        md += f"| {x['id']} | {x['language']} | {x['faithfulness']} | {x['answer_relevancy']} | {x['context_recall']} |\n"
    print(md)
    print("Saved:", save_results("ragas", {"summary": summary, "safety": safety, "failed": failed,
                                           "scores": scored, "answers": rows}, md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
