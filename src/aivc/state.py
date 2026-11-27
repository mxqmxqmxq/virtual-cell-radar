"""抓取水位线与运行漏斗状态（data/state/fetch_state.json）。"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

STATE_VERSION = 1
MAX_RUNS = 30
MAX_ERROR_LEN = 200
SOURCE_KEYS = ("last_success_date", "last_run_at", "last_fetched", "consecutive_failures", "last_error")
RUN_KEYS = (
    "run_at", "since", "until", "fetched", "kept_after_filter", "n_new", "n_updated",
    "n_duplicates", "llm_api_calls", "llm_errors", "fetch_errors", "incomplete",
)


def _iso_now(now: datetime) -> str:
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.isoformat()


@dataclass
class SourceState:
    last_success_date: date | None = None
    last_run_at: str | None = None
    last_fetched: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["last_success_date"] = self.last_success_date.isoformat() if self.last_success_date else None
        return {k: d[k] for k in SOURCE_KEYS}

    @classmethod
    def from_dict(cls, d: dict) -> "SourceState":
        lsd = d.get("last_success_date")
        try:
            lsd = date.fromisoformat(lsd) if lsd else None
        except (TypeError, ValueError):
            lsd = None
        return cls(
            last_success_date=lsd,
            last_run_at=d.get("last_run_at"),
            last_fetched=int(d.get("last_fetched") or 0),
            consecutive_failures=int(d.get("consecutive_failures") or 0),
            last_error=d.get("last_error"),
        )


class FetchState:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.sources: dict[str, SourceState] = {}
        self.runs: list[dict] = []

    @classmethod
    def load(cls, path: Path) -> "FetchState":
        st = cls(path)
        p = Path(path)
        if not p.exists():
            return st
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state root must be an object")
            for name, sd in (raw.get("sources") or {}).items():
                st.sources[name] = SourceState.from_dict(sd)
            runs = raw.get("runs") or []
            st.runs = [{k: r[k] for k in RUN_KEYS if k in r} for r in runs if isinstance(r, dict)][:MAX_RUNS]
        except (OSError, ValueError, TypeError, AttributeError) as e:
            log.warning("fetch_state 损坏，按空状态处理: %s", e)
            return cls(path)
        return st

    def to_dict(self) -> dict:
        return {
            "version": STATE_VERSION,
            "sources": {k: v.to_dict() for k, v in self.sources.items()},
            "runs": list(self.runs),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def _src(self, source: str) -> SourceState:
        return self.sources.setdefault(source, SourceState())

    def record_success(self, source: str, until: date, n_fetched: int, now: datetime) -> None:
        s = self._src(source)
        s.last_success_date = until
        s.last_run_at = _iso_now(now)
        s.last_fetched = n_fetched
        s.consecutive_failures = 0
        s.last_error = None

    def record_failure(self, source: str, error: str, now: datetime) -> None:
        s = self._src(source)
        s.last_run_at = _iso_now(now)
        s.consecutive_failures += 1
        s.last_error = str(error)[:MAX_ERROR_LEN]

    def record_incomplete(self, source: str, n_fetched: int, warning: str, now: datetime) -> None:
        """抓取结果不完整：更新运行时间与条数，但不推进 last_success_date，也不累加失败计数。"""
        s = self._src(source)
        s.last_run_at = _iso_now(now)
        s.last_fetched = n_fetched
        s.last_error = ("incomplete: " + str(warning))[:MAX_ERROR_LEN]

    def add_run(self, summary: dict) -> None:
        self.runs.insert(0, {k: summary[k] for k in RUN_KEYS if k in summary})
        del self.runs[MAX_RUNS:]

    def window_for(self, source: str, today: date, default_days: int,
                   overlap_days: int, max_catchup_days: int) -> date:
        since = today - timedelta(days=max(default_days, 1) - 1)
        s = self.sources.get(source)
        if s and s.last_success_date:
            since = min(since, s.last_success_date - timedelta(days=overlap_days))
        since = max(since, today - timedelta(days=max(max_catchup_days, 1) - 1))
        return min(since, today)
