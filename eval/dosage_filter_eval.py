"""Dosage-filter safety numbers: catch rate (recall), precision and false-block rate.

Runs filter detection on the labelled sentences in tests/dosage_cases.py and writes
eval/results/dosage_filter_<timestamp>.json and .md (used in report §5).

Usage:  python eval/dosage_filter_eval.py

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import RESULTS_DIR  # noqa: E402
from safety.dosage_filter import find_doses  # noqa: E402
from tests.dosage_cases import DOSE_NEGATIVE, DOSE_POSITIVE  # noqa: E402


def main() -> int:
    tp = sum(1 for s in DOSE_POSITIVE if find_doses(s))
    fn = len(DOSE_POSITIVE) - tp
    fp = sum(1 for s in DOSE_NEGATIVE if find_doses(s))
    tn = len(DOSE_NEGATIVE) - fp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    res = {
        "positives": len(DOSE_POSITIVE), "negatives": len(DOSE_NEGATIVE),
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "precision": round(precision, 3), "recall_catch_rate": round(recall, 3),
        "false_block_rate": round(fp / len(DOSE_NEGATIVE), 3),
        "missed": [s for s in DOSE_POSITIVE if not find_doses(s)],
        "false_blocks": [s for s in DOSE_NEGATIVE if find_doses(s)],
        "note": "Synthetic labelled sentences (EN + AR, incl. Arabic-Indic digits); not clinical data.",
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"dosage_filter_{stamp}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ("| Set | n | Blocked | Rate |\n|---|---|---|---|\n"
          f"| Dose-positive (should block) | {len(DOSE_POSITIVE)} | {tp} | catch rate {recall:.0%} |\n"
          f"| Normal answers (should pass) | {len(DOSE_NEGATIVE)} | {fp} | false-block rate {fp / len(DOSE_NEGATIVE):.0%} |\n"
          f"\nPrecision {precision:.2f} · Recall {recall:.2f}\n")
    (RESULTS_DIR / f"dosage_filter_{stamp}.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
