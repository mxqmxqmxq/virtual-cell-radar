import json
from datetime import date, datetime, timezone
from pathlib import Path

from aivc.state import MAX_RUNS, RUN_KEYS, FetchState

NOW = datetime(2026, 11, 28, 6, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 28)


def _win(st, src="arxiv"):
    return st.window_for(src, TODAY, default_days=3, overlap_days=1, max_catchup_days=14)


def test_load_missing(tmp_path):
    st = FetchState.load(tmp_path / "s" / "fetch_state.json")
    assert st.sources == {} and st.runs == []


def test_load_corrupt(tmp_path):
    p = tmp_path / "f.json"
    p.write_text("{not json", encoding="utf-8")
    st = FetchState.load(p)
    assert st.sources == {} and st.runs == []
    p.write_text("[1]", encoding="utf-8")
    assert FetchState.load(p).sources == {}


def test_success_failure_counts(tmp_path):
    st = FetchState(tmp_path / "f.json")
    st.record_failure("arxiv", "x" * 500, NOW)
    st.record_failure("arxiv", "boom", NOW)
    s = st.sources["arxiv"]
    assert s.consecutive_failures == 2 and s.last_error == "boom"
    st.record_failure("arxiv", "y" * 500, NOW)
    assert len(st.sources["arxiv"].last_error) == 200
    st.record_success("arxiv", TODAY, 7, NOW)
    s = st.sources["arxiv"]
    assert s.consecutive_failures == 0 and s.last_error is None
    assert s.last_success_date == TODAY and s.last_fetched == 7
    assert s.last_run_at.startswith("2026-11-28T06:00")


def test_runs_truncated_newest_first():
    st = FetchState(Path("unused.json"))
    for i in range(40):
        st.add_run({"run_at": str(i), "fetched": i, "junk": 1})
    assert len(st.runs) == MAX_RUNS == 30
    assert st.runs[0]["run_at"] == "39"
    assert set(st.runs[0]) <= set(RUN_KEYS)


def test_window_no_watermark():
    assert _win(FetchState(Path("f.json"))) == date(2026, 11, 26)


def test_window_catchup(tmp_path):
    st = FetchState(tmp_path / "f.json")
    st.record_success("arxiv", date(2026, 11, 25), 1, NOW)
    assert _win(st) == date(2026, 11, 24)


def test_window_clamped(tmp_path):
    st = FetchState(tmp_path / "f.json")
    st.record_success("arxiv", date(2026, 10, 19), 1, NOW)
    assert _win(st) == date(2026, 11, 15)  # today-13


def test_window_watermark_today_not_shorter(tmp_path):
    st = FetchState(tmp_path / "f.json")
    st.record_success("arxiv", TODAY, 1, NOW)
    assert _win(st) == date(2026, 11, 26)


def test_save_roundtrip_and_stable(tmp_path):
    p = tmp_path / "state" / "fetch_state.json"
    st = FetchState(p)
    st.record_success("biorxiv", TODAY, 3, NOW)
    st.record_failure("arxiv", "err", NOW)
    st.add_run({"run_at": "r", "since": "a", "until": "b", "fetched": 1})
    st.save()
    b1 = p.read_bytes()
    st.save()
    assert p.read_bytes() == b1
    assert not list(p.parent.glob("*.tmp"))
    txt = b1.decode()
    assert "/Users/" not in txt and "/home/" not in txt
    assert json.loads(txt)["version"] == 1
    st2 = FetchState.load(p)
    assert st2.sources["biorxiv"].last_success_date == TODAY
    assert st2.to_dict() == st.to_dict()


def test_record_incomplete_keeps_watermark_and_failures(tmp_path):
    st = FetchState(tmp_path / "f.json")
    st.record_success("openalex", TODAY, 5, NOW)
    st.record_failure("openalex", "earlier", NOW)
    st.record_success("openalex", TODAY, 5, NOW)
    before = st.sources["openalex"]
    assert before.consecutive_failures == 0
    later = datetime(2026, 11, 28, 9, 0, tzinfo=timezone.utc)
    st.record_incomplete("openalex", 12, "次页 401，保留首页", later)
    s = st.sources["openalex"]
    assert s.last_success_date == TODAY
    assert s.consecutive_failures == 0
    assert s.last_fetched == 12
    assert s.last_run_at.startswith("2026-11-28T09:00")
    assert s.last_error.startswith("incomplete: ")
    assert len(s.last_error) <= 200


def test_record_incomplete_truncates_long_warning(tmp_path):
    st = FetchState(tmp_path / "f.json")
    st.record_incomplete("hf_papers", 0, "w" * 500, NOW)
    s = st.sources["hf_papers"]
    assert s.last_error.startswith("incomplete: ")
    assert len(s.last_error) == 200
    assert s.last_success_date is None and s.consecutive_failures == 0


def test_record_incomplete_does_not_move_window(tmp_path):
    st = FetchState(tmp_path / "f.json")
    st.record_success("arxiv", date(2026, 11, 25), 1, NOW)
    st.record_incomplete("arxiv", 3, "partial", NOW)
    assert _win(st) == date(2026, 11, 24)


def test_incomplete_run_key_save_load_roundtrip(tmp_path):
    assert "incomplete" in RUN_KEYS
    p = tmp_path / "fetch_state.json"
    st = FetchState(p)
    st.add_run({"run_at": "r1", "fetched": 4, "incomplete": {"openalex": "次页 401"}})
    st.save()
    st2 = FetchState.load(p)
    assert st2.runs[0]["incomplete"] == {"openalex": "次页 401"}
    assert st2.to_dict() == st.to_dict()


def test_legacy_runs_without_incomplete_key_load(tmp_path):
    p = tmp_path / "fetch_state.json"
    p.write_text(
        json.dumps({
            "version": 1,
            "sources": {},
            "runs": [{"run_at": "old", "fetched": 2, "llm_api_calls": 1}],
        }),
        encoding="utf-8",
    )
    st = FetchState.load(p)
    assert len(st.runs) == 1
    assert st.runs[0]["run_at"] == "old"
    assert "incomplete" not in st.runs[0]
