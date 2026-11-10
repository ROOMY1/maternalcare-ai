"""Retrieval evaluation without an LLM judge (reliable for Arabic too).

Measures
    hit@1, hit@k and MRR against the gold (source, page) of each benchmark question.
    --calibrate      : top-1 similarity of in-scope vs out-of-scope questions, picks the threshold
                       that best separates them and (with --write-env) writes it to .env.
    --chunk-sizes    : the chunk-size experiment (e.g. 250 400 600) for report §2.1. Each size is
                       ingested and indexed into a temporary Chroma, then measured the same way.

Usage
    python eval/retrieval_eval.py
    python eval/retrieval_eval.py --calibrate --write-env
    python eval/retrieval_eval.py --chunk-sizes 250 400 600

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import argparse
import re
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from common import (CALIBRATION_OUT_OF_SCOPE, ROOT, gold_pages, load_benchmark, save_results)

from config import get_settings
from rag.retriever import Retriever, get_retriever


# How a question is turned into search queries (set in main() from --translate / --normalize).
QUERY_MODE = {"translate": False, "normalize": False}
_LLM: Any = None
_EN_CACHE: dict[str, str] = {}


def search(retriever: Retriever, text: str, k: int, threshold: float) -> list[Any]:
    """Search the way the pipeline does: Arabic questions can be normalized and/or also searched
    with an English query written by the LLM (rag/query_transform.py)."""
    from safety.arabic_text import detect_language, normalize

    queries = [text]
    if detect_language(text) == "ar":
        if QUERY_MODE["normalize"]:
            queries = [normalize(text)]
        if QUERY_MODE["translate"]:
            global _LLM
            if _LLM is None:
                from rag.llm import get_chat_model

                _LLM = get_chat_model()
            if text not in _EN_CACHE:
                from rag.query_transform import to_english_query

                _EN_CACHE[text] = to_english_query(text, _LLM)
            if _EN_CACHE[text]:
                queries.append(_EN_CACHE[text])
    return retriever.search_multi(queries, k, threshold)


def rank_metrics(retriever: Retriever, queries: list[dict[str, Any]], k: int) -> dict[str, Any]:
    """hit@1, hit@k, MRR@k over the in-scope questions (threshold 0: pure ranking quality)."""
    rows = []
    for q in queries:
        gold = gold_pages(q)
        if not gold:
            continue
        hits = search(retriever, q["question"], k, 0.0)
        rank = next((i for i, h in enumerate(hits, 1) if (h.source, h.page) in gold), None)
        gold_docs = {src for src, _ in gold}
        doc_rank = next((i for i, h in enumerate(hits, 1) if h.source in gold_docs), None)
        rows.append({"id": q["id"], "language": q["language"], "rank": rank, "doc_rank": doc_rank,
                     "top1": f"{hits[0].source} p.{hits[0].page}" if hits else "",
                     "top1_similarity": hits[0].similarity if hits else 0.0})

    def summary(sub: list[dict[str, Any]]) -> dict[str, float]:
        n = len(sub) or 1
        return {"n": len(sub),
                "hit@1": round(sum(r["rank"] == 1 for r in sub) / n, 3),
                f"hit@{k}": round(sum(r["rank"] is not None for r in sub) / n, 3),
                "mrr": round(sum(1 / r["rank"] for r in sub if r["rank"]) / n, 3),
                f"doc_hit@{k}": round(sum(r["doc_rank"] is not None for r in sub) / n, 3)}

    return {"all": summary(rows), "en": summary([r for r in rows if r["language"] == "en"]),
            "ar": summary([r for r in rows if r["language"] == "ar"]), "rows": rows}


def calibrate(retriever: Retriever, queries: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the threshold that best separates in-scope from out-of-scope top-1 similarities."""
    def top1(text: str) -> float:
        hits = search(retriever, text, 1, 0.0)
        return hits[0].similarity if hits else 0.0

    pos = [top1(q["question"]) for q in queries if q["type"] != "out_of_scope"]
    neg_texts = [q["question"] for q in queries if q["type"] == "out_of_scope"]
    neg_texts += [t for t in CALIBRATION_OUT_OF_SCOPE if t not in neg_texts]
    neg = [top1(t) for t in neg_texts]

    best, plateau = -1.0, []
    for i in range(30, 81):
        t = i / 100
        bal_acc = (sum(p >= t for p in pos) / len(pos) + sum(n < t for n in neg) / len(neg)) / 2
        if bal_acc > best + 1e-9:
            best, plateau = bal_acc, [t]
        elif abs(bal_acc - best) < 1e-9:
            plateau.append(t)
    chosen = round(statistics.median(plateau), 2)  # middle of the best range = largest margin
    return {"in_scope_top1": sorted(round(p, 3) for p in pos),
            "out_of_scope_top1": sorted(round(n, 3) for n in neg),
            "in_scope_min": round(min(pos), 3), "out_of_scope_max": round(max(neg), 3),
            "best_balanced_accuracy": round(best, 3),
            "best_range": [plateau[0], plateau[-1]], "chosen_threshold": chosen,
            "in_scope_kept": sum(p >= chosen for p in pos), "in_scope_total": len(pos),
            "out_of_scope_blocked": sum(n < chosen for n in neg), "out_of_scope_total": len(neg)}


def write_env_threshold(value: float) -> None:
    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8")
    new = re.sub(r"(?m)^SIMILARITY_THRESHOLD=.*$",
                 f"SIMILARITY_THRESHOLD={value}               # calibrated by eval/retrieval_eval.py", text)
    env.write_text(new, encoding="utf-8")


def chunk_size_experiment(sizes: list[int], queries: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Re-chunk and re-index into a temp Chroma for each size; measure ranking quality."""
    from rag.embeddings import get_embedder
    from rag.index_builder import build_index, get_client
    from rag.ingestion import ingest, load_tokenizer, read_chunks

    s = get_settings()
    tok = load_tokenizer(s.embed_model)
    emb = get_embedder()
    out = []
    for size in sizes:
        overlap = round(size * 0.15)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            t0 = time.perf_counter()
            path = Path(tmp) / "chunks.json"
            summ = ingest(out_path=path, chunk_size=size, chunk_overlap=overlap, tokenizer=tok)
            _, chunks = read_chunks(path)
            client = get_client(Path(tmp) / "vdb")
            build_index(chunks, emb, client, "exp_chunks", size, overlap)
            m = rank_metrics(Retriever(emb, client.get_collection("exp_chunks")), queries, k)
            out.append({"chunk_size": size, "overlap": overlap, "chunks": summ.chunks,
                        "build_seconds": round(time.perf_counter() - t0, 1),
                        **{f"all_{a}": b for a, b in m["all"].items()},
                        "ar_hit@1": m["ar"]["hit@1"], "en_hit@1": m["en"]["hit@1"]})
            print(out[-1])
            del client
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--write-env", action="store_true", help="write the calibrated threshold to .env")
    ap.add_argument("--chunk-sizes", type=int, nargs="*")
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--translate", action="store_true", help="Arabic: also search with an English query")
    ap.add_argument("--normalize", action="store_true", help="Arabic: normalize the question first")
    ap.add_argument("--tag", default="", help="suffix for the result file name")
    args = ap.parse_args()
    s = get_settings()
    k = args.k or s.top_k
    queries = load_benchmark()
    QUERY_MODE.update(translate=args.translate, normalize=args.normalize)

    if args.chunk_sizes:
        rows = chunk_size_experiment(args.chunk_sizes, queries, k)
        md = ("| Chunk size (tokens) | Overlap | Chunks | hit@1 | hit@{k} | MRR | EN hit@1 | AR hit@1 | Build s |\n"
              "|---|---|---|---|---|---|---|---|---|\n").replace("{k}", str(k))
        for r in rows:
            md += (f"| {r['chunk_size']} | {r['overlap']} | {r['chunks']} | {r['all_hit@1']} | "
                   f"{r[f'all_hit@{k}']} | {r['all_mrr']} | {r['en_hit@1']} | {r['ar_hit@1']} | "
                   f"{r['build_seconds']} |\n")
        print(md)
        print("Saved:", save_results("chunk_size_experiment", {"k": k, "rows": rows}, md))
        return 0

    retriever = get_retriever()
    m = rank_metrics(retriever, queries, k)
    md = (f"### Retrieval quality (top-{k}, gold = source + page, no LLM)\n\n"
          f"| Subset | n | page hit@1 | page hit@{k} | page MRR | document hit@{k} |\n"
          "|---|---|---|---|---|---|\n")
    for name in ("all", "en", "ar"):
        r = m[name]
        md += (f"| {name} | {r['n']} | {r['hit@1']} | {r[f'hit@{k}']} | {r['mrr']} | "
               f"{r[f'doc_hit@{k}']} |\n")
    md += ("\nPage hit = the exact gold page (or a listed alternative) is retrieved; document hit = any "
           "page of the gold document is retrieved (the same recommendation is often repeated on "
           "summary pages).\n\n| id | lang | gold page rank | gold doc rank | top-1 retrieved | "
           "top-1 similarity |\n|---|---|---|---|---|---|\n")
    for r in m["rows"]:
        md += (f"| {r['id']} | {r['language']} | {r['rank'] or 'miss'} | {r['doc_rank'] or 'miss'} | "
               f"{r['top1']} | {r['top1_similarity']:.3f} |\n")
    data: dict[str, Any] = {"k": k, "metrics": m}

    if args.calibrate:
        c = calibrate(retriever, queries)
        data["calibration"] = c
        md += (f"\n### Threshold calibration\n\n"
               f"- In-scope top-1 similarity: min {c['in_scope_min']}, values {c['in_scope_top1']}\n"
               f"- Out-of-scope top-1 similarity: max {c['out_of_scope_max']}, values {c['out_of_scope_top1']}\n"
               f"- Best balanced accuracy {c['best_balanced_accuracy']} for thresholds "
               f"{c['best_range'][0]}–{c['best_range'][1]}; chosen (middle) **{c['chosen_threshold']}**\n"
               f"- At the chosen threshold: in-scope kept {c['in_scope_kept']}/{c['in_scope_total']}, "
               f"out-of-scope blocked {c['out_of_scope_blocked']}/{c['out_of_scope_total']}\n")
        if args.write_env:
            write_env_threshold(c["chosen_threshold"])
            md += f"- Written to `.env` as SIMILARITY_THRESHOLD={c['chosen_threshold']}\n"
    print(md)
    data["query_mode"] = dict(QUERY_MODE)
    data["english_queries"] = _EN_CACHE
    md = f"Query mode: {QUERY_MODE}\n\n" + md
    print("Saved:", save_results("retrieval_eval" + (f"_{args.tag}" if args.tag else ""), data, md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
