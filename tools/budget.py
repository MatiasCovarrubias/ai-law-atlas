"""Hard cap on API spend. Every API call in this repo goes through `guard` (before) and `record` (after).

    python tools/budget.py                 # spent so far, cap, headroom
    python tools/budget.py add 2.31 "label" # record spend made outside the guard (e.g. runs started before it)

The user's budget is USD 100 in total. Calls stop at STOP_AT (below the cap) so that calls already in flight,
parallel processes and unlogged retries cannot push spend over it. `guard` reserves the worst case of the call
(full prompt plus max_tokens of output at the price tier that applies) and refuses it if spent + reserved + this
call would pass STOP_AT. The ledger (logs/api_spend.jsonl) is committed, so it survives deleting build/.
The workspace spend limit set in the Anthropic Console is a second, independent backstop.
"""

from __future__ import annotations

import fcntl
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "logs" / "api_spend.jsonl"
CAP_USD = 100.0
STOP_AT = 95.0

_lock = threading.Lock()
_reserved = 0.0  # worst-case cost of calls in flight in this process


class BudgetExceeded(RuntimeError):
    """Refused: the call could take total API spend past STOP_AT."""


def spent() -> float:
    if not LEDGER.exists():
        return 0.0
    with LEDGER.open() as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        return sum(json.loads(l)["usd"] for l in fh if l.strip())


def _append(row: dict) -> None:
    LEDGER.parent.mkdir(exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(row) + "\n")


def guard(worst_case_usd: float) -> None:
    """Reserve `worst_case_usd` for one call, or raise BudgetExceeded. Pair with `release` and `record`."""
    global _reserved
    with _lock:
        total = spent() + _reserved + worst_case_usd
        if total > STOP_AT:
            raise BudgetExceeded(f"API budget: spent ${spent():.2f}, in flight ${_reserved:.2f}, this call up to "
                                 f"${worst_case_usd:.2f}; stop at ${STOP_AT:.0f} (cap ${CAP_USD:.0f})")
        _reserved += worst_case_usd


def release(worst_case_usd: float) -> None:
    global _reserved
    with _lock:
        _reserved = max(0.0, _reserved - worst_case_usd)


def record(usd: float, label: str) -> None:
    _append({"at": datetime.now().isoformat(timespec="seconds"), "usd": round(usd, 6), "label": label})


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "add":
        record(float(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "manual")
    s = spent()
    print(f"spent ${s:.2f} of ${CAP_USD:.0f} (calls stop at ${STOP_AT:.0f}); headroom ${STOP_AT - s:.2f}")


if __name__ == "__main__":
    main()
