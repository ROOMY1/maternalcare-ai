"""Shared helpers for the evaluation scripts: benchmark loading, gold pages, hardware info, saving.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import EVAL_DIR, RESULTS_DIR, get_settings  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass

BENCHMARK = EVAL_DIR / "benchmark_queries.json"

# Off-topic probes used ONLY to calibrate the threshold (more negatives than the 3 benchmark ones).
# They are deliberately unrelated to maternal care; no clinical content.
CALIBRATION_OUT_OF_SCOPE = [
    "What is the capital of France?", "How do I change a flat car tyre?",
    "Who won the 2022 football World Cup?", "How do I install Python on Windows?",
    "What is the best laptop for gaming?", "Give me a recipe for chocolate cake.",
    "How do I reset my email password?", "What is the exchange rate of the dollar today?",
    "How does a car engine work?", "Explain the rules of chess.",
    "How do I treat a sprained ankle from football?", "What causes dandruff in men?",
    "مين كسب كاس العالم ٢٠٢٢؟", "إزاي أغير كاوتش العربية؟", "إيه أحسن موبايل أشتريه؟",
    "إزاي أعمل كيكة شوكولاتة؟", "سعر الدولار النهارده كام؟", "إزاي أذاكر برمجة بايثون؟",
]


def load_benchmark() -> list[dict[str, Any]]:
    return json.loads(BENCHMARK.read_text(encoding="utf-8"))["queries"]


def gold_pages(q: dict[str, Any]) -> set[tuple[str, int]]:
    """All (source, page) pairs that count as a correct retrieval for a query."""
    gold = {(q["source"], int(q["page"]))} if q.get("source") else set()
    gold |= {(a["source"], int(a["page"])) for a in q.get("alt", [])}
    return gold


def hardware_info() -> dict[str, str]:
    info = {"python": platform.python_version(), "os": platform.platform(),
            "cpu": platform.processor()}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        info["gpu"] = out or "none"
    except (OSError, subprocess.SubprocessError):
        info["gpu"] = "none"
    s = get_settings()
    info.update(llm=s.llm_model, judge=s.judge_model, embedding=s.embed_model,
                top_k=str(s.top_k), threshold=str(s.similarity_threshold))
    return info


def vram_used_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out.splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def save_results(name: str, data: dict[str, Any], markdown: str) -> Path:
    """Write eval/results/<name>_<timestamp>.json and .md (the .md goes into the report)."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data = {"created": stamp, "hardware": hardware_info(), **data}
    (RESULTS_DIR / f"{name}_{stamp}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path = RESULTS_DIR / f"{name}_{stamp}.md"
    md_path.write_text(markdown, encoding="utf-8")
    return md_path


def strip_disclaimer(answer: str) -> str:
    """The disclaimer is added by code; evaluation looks at the answer part only."""
    return answer.split("\n\n---\n")[0].strip()
