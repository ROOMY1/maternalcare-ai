"""Command-line interface for MaternalCare AI.

Usage
    python cli.py ingest                          PDFs -> data/processed/processed_chunks.json
    python cli.py index [--rebuild] [--yes]       chunks -> ChromaDB (vector_db/)
    python cli.py ask "question"                  full RAG answer
    python cli.py ask --retrieve-only "question"  only show the retrieved chunks
    python cli.py initdb                          create the SQLite schema (db/patient_records.db)
    python cli.py eval {retrieval,ragas,baseline} run an evaluation script

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys

from config import BASE_DIR, get_settings

# Windows consoles default to a legacy code page; force UTF-8 so Arabic prints correctly.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass


def cmd_ingest(_: argparse.Namespace) -> int:
    from rag.ingestion import ingest

    summary = ingest()
    print(summary.as_text())
    print("Chunks per document:")
    for name, n in summary.per_document.items():
        print(f"  {n:5d}  {name}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from rag.index_builder import build_from_file

    if args.rebuild and not args.yes:
        reply = input("This deletes and rebuilds the vector index. Continue? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1
    stats = build_from_file(rebuild=args.rebuild)
    print(f"Chunks in file: {stats.chunks_in_file} | collection count: {stats.collection_count} "
          f"| {stats.seconds:.1f} s")
    if stats.sample_hit:
        print(f"Sample query: {stats.sample_query!r} -> {stats.sample_hit}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    question = " ".join(args.question)
    if args.retrieve_only:
        from rag.retriever import retrieve

        chunks = retrieve(question, k=args.k, threshold=args.threshold, category=args.category)
        if not chunks:
            print("No chunk passed the similarity threshold.")
        for i, c in enumerate(chunks, 1):
            snippet = c.text[:220].replace("\n", " ")
            print(f"[{i}] similarity {c.similarity:.3f} | {c.source} p.{c.page} | {c.category}\n"
                  f"    {snippet}...")
        return 0

    from rag.rag_pipeline import answer_question

    resp = answer_question(question, k=args.k, threshold=args.threshold)
    if args.json:
        print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
        return 0
    print(resp.answer)  # starts with the emergency banner when a danger sign was detected
    if resp.query_used:
        print(f"\n(English search query used: {resp.query_used})")
    label = "Cited sources" if resp.cited else "Retrieved sources"
    if resp.citations:
        print(f"\n{label}:")
        for c in resp.citations:
            mark = f"[{c.marker}] " if c.marker else ""
            print(f"  {mark}{c.source} p.{c.page} (similarity score {c.similarity_score:.2f})")
    flags = [f for f, on in (("NOT FOUND", resp.not_found), ("DOSAGE FILTERED", resp.dosage_filtered)) if on]
    if flags:
        print("Flags: " + ", ".join(flags))
    l = resp.latency_ms
    print(f"Latency ms: embed {l.embed:.0f} | search {l.search:.0f} | llm {l.llm:.0f} | total {l.total:.0f}")
    return 0


def cmd_initdb(_: argparse.Namespace) -> int:
    from storage.database import init_db

    print(f"Database ready: {init_db()}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    script = {"retrieval": "retrieval_eval.py", "ragas": "run_ragas.py",
              "baseline": "baseline_vs_rag.py"}[args.which]
    return subprocess.call([sys.executable, str(BASE_DIR / "eval" / script), *args.rest])


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "sentence_transformers", "chromadb", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    get_settings()  # applies telemetry/offline environment variables early

    p = argparse.ArgumentParser(prog="cli.py", description="MaternalCare AI (local RAG)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="PDFs -> processed_chunks.json").set_defaults(func=cmd_ingest)
    pi = sub.add_parser("index", help="chunks -> ChromaDB")
    pi.add_argument("--rebuild", action="store_true", help="delete and re-create the collection")
    pi.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    pi.set_defaults(func=cmd_index)
    pa = sub.add_parser("ask", help="ask a question")
    pa.add_argument("question", nargs="+")
    pa.add_argument("--retrieve-only", action="store_true")
    pa.add_argument("--k", type=int, default=None)
    pa.add_argument("--threshold", type=float, default=None)
    pa.add_argument("--category", default=None)
    pa.add_argument("--json", action="store_true", help="print the full RAGResponse as JSON")
    pa.set_defaults(func=cmd_ask)
    sub.add_parser("initdb", help="create the SQLite schema").set_defaults(func=cmd_initdb)
    pe = sub.add_parser("eval", help="run an evaluation script")
    pe.add_argument("which", choices=["retrieval", "ragas", "baseline"])
    pe.add_argument("rest", nargs=argparse.REMAINDER)
    pe.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # friendly one-line error with the fix, no traceback
        if os.environ.get("MC_DEBUG"):  # set MC_DEBUG=1 to see the full traceback
            raise
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
