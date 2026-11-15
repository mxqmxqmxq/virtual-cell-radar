from datetime import date

from aivc.models import Paper
from aivc.storage import PaperStore


def P(i, pub=None, **kw):
    return Paper(id=i, title=kw.pop("title", i), source="other", published=pub, first_seen=date(2026, 3, 5), **kw)


def test_monthly_split_and_roundtrip(tmp_path):
    s = PaperStore(tmp_path)
    assert s.upsert([P("b", date(2026, 1, 2)), P("a", date(2026, 1, 3)), P("c", date(2026, 2, 1)), P("d")]) == (4, 0)
    names = sorted(f.name for f in (tmp_path / "papers").glob("*.jsonl"))
    assert names == ["2026-01.jsonl", "2026-02.jsonl", "2026-03.jsonl"]
    lines = (tmp_path / "papers/2026-01.jsonl").read_text().splitlines()
    assert [__import__("json").loads(l)["id"] for l in lines] == ["a", "b"]
    assert {p.id for p in s.load_all()} == {"a", "b", "c", "d"}


def test_idempotent(tmp_path):
    s = PaperStore(tmp_path)
    ps = [P("a", date(2026, 1, 2)), P("b", date(2026, 2, 2))]
    s.upsert(ps)
    before = {f.name: f.read_bytes() for f in (tmp_path / "papers").glob("*.jsonl")}
    assert s.upsert(ps) == (0, 0)
    after = {f.name: f.read_bytes() for f in (tmp_path / "papers").glob("*.jsonl")}
    assert before == after


def test_update_keeps_file(tmp_path):
    s = PaperStore(tmp_path)
    s.upsert([P("a", date(2026, 1, 2))])
    assert s.upsert([P("a", date(2026, 5, 9), title="new")]) == (0, 1)
    files = [f.name for f in (tmp_path / "papers").glob("*.jsonl")]
    assert files == ["2026-01.jsonl"]
    assert s.load_all()[0].title == "new"


def test_chinese_not_escaped(tmp_path):
    s = PaperStore(tmp_path)
    s.upsert([P("a", date(2026, 1, 2), title="虚拟细胞")])
    assert "虚拟细胞" in (tmp_path / "papers/2026-01.jsonl").read_text(encoding="utf-8")


def test_empty_dir(tmp_path):
    assert PaperStore(tmp_path).load_all() == []


def test_load_overlay(tmp_path):
    from aivc.storage import load_overlay
    pub, loc = tmp_path / "pub", tmp_path / "loc"
    PaperStore(pub).upsert([P("a", date(2026, 1, 2), title="PUB", tags=["x"]), P("b", date(2026, 1, 3))])
    PaperStore(loc).upsert([P("a", date(2026, 1, 2), title="LOC", tags=["y", "x"], local_file="f.pdf"),
                            P("z", date(2026, 1, 4))])
    before = {f: f.read_bytes() for d in (pub, loc) for f in d.rglob("*.jsonl")}
    got = {p.id: p for p in load_overlay(pub, loc)}
    assert set(got) == {"a", "b", "z"}
    assert got["a"].title == "PUB" and got["a"].tags == ["x", "y"] and got["a"].local_file == "f.pdf"
    assert got["b"].local_file is None
    assert before == {f: f.read_bytes() for d in (pub, loc) for f in d.rglob("*.jsonl")}
    assert len(load_overlay(pub, tmp_path / "nope")) == 2
