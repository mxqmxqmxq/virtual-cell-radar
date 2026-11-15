import json
from datetime import date, datetime, timezone

import httpx
import pytest

from aivc import cli, openreview_refresh as orf
from aivc.config import load_config
from aivc.models import LLMAnnotation, Paper
from aivc.storage import PaperStore

TODAY = date(2026, 11, 14)
OLD_ABS = "We build a virtual cell model for perturbation prediction on Perturb-seq data."


def llm():
    return LLMAnnotation(relevance=8, model="m", prompt_version="v3",
                         scored_at=datetime(2026, 11, 1, tzinfo=timezone.utc), circle="core")


def paper(nid, abstract=OLD_ABS, **kw):
    base = dict(id=f"openreview:{nid}", title="Old title virtual cell", abstract=abstract, source="openreview",
                openreview_id=nid, venue="ICLR 2027 (submission, under review)", first_seen=TODAY,
                tags=["ICLR2027"], llm_status="scored", llm=llm(), final_score=9.0, must_read=True)
    base.update(kw)
    return Paper(**base)


def note(nid, abstract=OLD_ABS, **extra):
    n = {"id": nid, "cdate": 1760000000000, "content": {
        "title": {"value": "Official virtual cell title"}, "abstract": {"value": abstract},
        "keywords": {"value": ["perturbation", "virtual cell"]},
        "authors": {"value": ["Anonymous"]}, "venue": {"value": "ICLR 2027 Conference Submission"}}}
    n.update(extra)
    return n


class Env:
    def __init__(self, tmp_path, handler):
        self.cfg = load_config(__import__("pathlib").Path(__file__).parent.parent / "config")
        self.data, self.local = tmp_path / "data", tmp_path / "local"
        self.calls: list[str] = []
        self.sleeps: list[float] = []

        def h(req: httpx.Request):
            nid = req.url.params["id"]
            self.calls.append(nid)
            return handler(nid)

        self.client = httpx.Client(transport=httpx.MockTransport(h))

    def run(self, **kw):
        return orf.run_refresh(self.cfg, data_dir=self.data, local_dir=self.local, client=self.client,
                               sleep=self.sleeps.append, today=TODAY, out=lambda m: None, **kw)

    def pub(self):
        return {p.openreview_id: p for p in PaperStore(self.data).load_all()}

    def loc(self):
        return {p.openreview_id: p for p in PaperStore(self.local / "data").load_all()}


def ok(nid):
    return httpx.Response(200, json={"notes": [note(nid)], "count": 1})


def test_overwrite_keeps_score_when_abstract_unchanged(tmp_path):
    e = Env(tmp_path, ok)
    PaperStore(e.data).upsert([paper("aaa")])
    s = e.run()
    p = e.pub()["aaa"]
    assert (s.overwritten, s.abstract_changed, s.moved, s.failed) == (1, 0, 0, [])
    assert p.title == "Official virtual cell title" and p.keywords == ["perturbation", "virtual cell"]
    assert p.venue == "ICLR 2027 Conference Submission" and p.authors == []
    assert p.published == date(2025, 10, 9) and p.updated == TODAY
    assert p.llm_status == "scored" and p.llm is not None and p.final_score == 9.0 and p.must_read


def test_abstract_change_resets_scoring(tmp_path):
    e = Env(tmp_path, lambda nid: httpx.Response(200, json={"notes": [note(nid, "A different virtual cell abstract.")]}))
    PaperStore(e.data).upsert([paper("aaa")])
    s = e.run()
    p = e.pub()["aaa"]
    assert s.abstract_changed == 1
    assert (p.llm_status, p.llm, p.final_score, p.must_read, p.llm_skip_reason) == \
        ("unscored", None, None, False, "abstract_changed")
    assert p.abstract == "A different virtual cell abstract."


def test_whitespace_only_abstract_diff_is_unchanged(tmp_path):
    e = Env(tmp_path, lambda nid: httpx.Response(200, json={"notes": [note(nid, OLD_ABS.replace(" ", "  ") + "\n")]}))
    PaperStore(e.data).upsert([paper("aaa")])
    assert e.run().abstract_changed == 0
    assert e.pub()["aaa"].llm_status == "scored"


def test_404_moves_to_local(tmp_path):
    e = Env(tmp_path, lambda nid: ok(nid) if nid != "bad" else httpx.Response(404, json={}))
    PaperStore(e.data).upsert([paper("aaa"), paper("bad", local_file="x/y.pdf", tags=["ICLR2027", "private"])])
    s = e.run()
    assert s.moved == 1 and set(e.pub()) == {"aaa"}
    assert e.loc()["bad"].tags == ["ICLR2027", "private"]


def test_empty_notes_and_ddate_count_as_gone(tmp_path):
    def h(nid):
        if nid == "e1":
            return httpx.Response(200, json={"notes": []})
        if nid == "e2":
            return httpx.Response(200, json={"notes": [note(nid, ddate=1)]})
        return ok(nid)
    e = Env(tmp_path, h)
    PaperStore(e.data).upsert([paper("e1"), paper("e2"), paper("aaa")])
    assert e.run().moved == 2 and set(e.pub()) == {"aaa"}


def test_5xx_429_and_bad_json_do_not_move(tmp_path):
    def h(nid):
        return {"s5": httpx.Response(503), "s4": httpx.Response(429),
                "js": httpx.Response(200, text="not json"),
                "nc": httpx.Response(200, json={"notes": [{"id": "nc", "content": []}]})}.get(nid) or ok(nid)
    e = Env(tmp_path, h)
    PaperStore(e.data).upsert([paper(x) for x in ("s5", "s4", "js", "nc", "aaa")])
    before = {k: v.model_dump() for k, v in e.pub().items() if k != "aaa"}
    s = e.run()
    assert s.moved == 0 and len(s.failed) == 4 and s.overwritten == 1
    assert {k: v.model_dump() for k, v in e.pub().items() if k != "aaa"} == before
    assert not (e.local / "data").exists()


def test_transport_error_is_transient(tmp_path):
    def h(nid):
        raise httpx.ConnectTimeout("t")
    e = Env(tmp_path, h)
    PaperStore(e.data).upsert([paper("aaa")])
    s = e.run()
    assert len(s.failed) == 1 and s.moved == 0 and "aaa" in e.pub()


def test_all_gone_guard_does_not_move(tmp_path):
    e = Env(tmp_path, lambda nid: httpx.Response(403))
    PaperStore(e.data).upsert([paper(x) for x in ("a", "b", "c")])
    s = e.run()
    assert s.guard_tripped and s.moved == 0 and len(e.pub()) == 3 and len(s.failed) == 3


def test_dry_run_writes_nothing(tmp_path):
    e = Env(tmp_path, lambda nid: ok(nid) if nid != "bad" else httpx.Response(404))
    PaperStore(e.data).upsert([paper("aaa"), paper("bad"), paper("zzz")])
    snap = {f.name: f.read_bytes() for f in (e.data / "papers").glob("*")}
    s = e.run(dry_run=True)
    assert (s.overwritten, s.moved) == (2, 1)
    assert {f.name: f.read_bytes() for f in (e.data / "papers").glob("*")} == snap
    assert not e.local.exists()


def test_limit(tmp_path):
    e = Env(tmp_path, ok)
    PaperStore(e.data).upsert([paper(x) for x in ("a", "b", "c")])
    s = e.run(limit=2)
    assert s.total == 2 and e.calls == ["openreview:a"[11:], "b"]


def test_interval_at_least_one_second(tmp_path):
    e = Env(tmp_path, ok)
    e.cfg.sources.openreview.delay_seconds = 0.2
    PaperStore(e.data).upsert([paper(x) for x in ("a", "b", "c")])
    e.run()
    assert e.sleeps == [1.0, 1.0]
    e2 = Env(tmp_path / "x", ok)
    e2.cfg.sources.openreview.delay_seconds = 2.5
    PaperStore(e2.data).upsert([paper(x) for x in ("a", "b")])
    e2.run()
    assert e2.sleeps == [2.5]


def test_ignores_non_openreview_and_rerun_is_idempotent(tmp_path):
    e = Env(tmp_path, ok)
    other = Paper(id="arxiv:1", title="t", source="arxiv", first_seen=TODAY)
    PaperStore(e.data).upsert([paper("aaa"), other])
    e.run()
    snap = {k: v.model_dump() for k, v in e.pub().items()}
    e.run()
    assert {k: v.model_dump() for k, v in e.pub().items()} == snap
    assert e.calls == ["aaa", "aaa"]


def test_cli_help_and_dry_run(tmp_path, config_dir, monkeypatch, capsys):
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(["refresh-openreview", "--help"])
    out = capsys.readouterr().out
    assert "--dry-run" in out and "--limit" in out
    PaperStore(tmp_path / "data").upsert([paper("aaa")])
    seen = {}

    def fake(cfg, **kw):
        seen.update(kw)
        return orf.RefreshSummary()
    monkeypatch.setattr(orf, "run_refresh", fake)
    rc = cli.main(["--config-dir", str(config_dir), "--data-dir", str(tmp_path / "data"),
                   "refresh-openreview", "--dry-run", "--limit", "3"])
    assert rc == 0 and seen["dry_run"] is True and seen["limit"] == 3
