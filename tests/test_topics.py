import json
from datetime import date
from pathlib import Path

import pytest

from aivc.models import Paper
from aivc.termmatch import compile_term, paper_text
from aivc.topics import Topic, load_topics, match_topics, merge_topics, report_topics

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "term_match_cases.json").read_text(encoding="utf-8")
)


def test_case_file_shape():
    assert CASES["version"] == 1 and len(CASES["cases"]) >= 25


@pytest.mark.parametrize("c", CASES["cases"], ids=lambda c: f"{c['term']}|{c['note']}")
def test_shared_cases(c):
    pat = compile_term(c["term"])
    assert bool(pat.search(c["text"])) is c["match"]


def mk(title, abstract=None, keywords=()):
    return Paper(id="x:1", title=title, abstract=abstract, source="other",
                 keywords=list(keywords), first_seen=date(2026, 1, 1))


def T(id, terms, **kw):
    return Topic(id=id, name=id, terms=terms, **kw)


def test_paper_text():
    assert paper_text("T", None, ["a", "b"]) == "T\n\na ; b"


def test_topic_validation():
    with pytest.raises(ValueError):
        T("Bad Id", ["abc"])
    with pytest.raises(ValueError):
        T("ok", ["a"])
    with pytest.raises(ValueError):
        T("ok", ["***"])
    with pytest.raises(ValueError):
        T("ok", [])
    with pytest.raises(ValueError):
        Topic(id="ok", name="  ", terms=["abc"])
    assert T("ok", ["Abc", "abc", " ab\x01c "]).terms == ["Abc"]


def test_load_topics(tmp_path):
    assert load_topics(tmp_path / "nope.yaml") == []
    f = tmp_path / "t.yaml"
    f.write_text("")
    assert load_topics(f) == []
    f.write_text("topics:\n  - {id: a, name: A, terms: [abc]}\n  - {id: b, name: B, terms: [xyz], default_enabled: true}\n")
    ts = load_topics(f)
    assert [t.id for t in ts] == ["a", "b"] and ts[1].default_enabled
    f.write_text("topics:\n  - {id: a, name: A, terms: [abc]}\n  - {id: a, name: B, terms: [xyz]}\n")
    with pytest.raises(ValueError, match="t.yaml"):
        load_topics(f)
    f.write_text("topics:\n  - {id: BAD, name: A, terms: [abc]}\n")
    with pytest.raises(ValueError, match="t.yaml"):
        load_topics(f)
    f.write_text("topics: [" + ",".join(f"{{id: a{i}, name: A, terms: [abc]}}" for i in range(21)) + "]\n")
    with pytest.raises(ValueError, match="t.yaml"):
        load_topics(f)


def test_merge_and_report():
    a, b = T("a", ["abc"]), T("b", ["bcd"], default_enabled=True)
    b2 = T("b", ["new"]); c = T("c", ["cde"])
    m = merge_topics([a, b], [b2, c])
    assert [t.id for t in m] == ["a", "b", "c"] and m[1].terms == ["new"]
    assert [t.id for t in report_topics([a, b])] == ["b"]


def test_match_topics():
    ts = [T("a", ["virtual cell", "foo bar"]), T("b", ["zzz"])]
    assert [t.id for t in match_topics(mk("Virtual cells rock"), ts)] == ["a"]
    assert [t.id for t in match_topics(mk("x", "about foo-bar"), ts)] == ["a"]
    assert [t.id for t in match_topics(mk("x", None, ["zzz"]), ts)] == ["b"]
    assert match_topics(mk("nothing"), ts) == []


def test_real_topics_yaml(config_dir):
    ts = load_topics(Path(config_dir) / "topics.yaml")
    assert len(ts) == 3 and not any(t.default_enabled for t in ts)
    blob = json.dumps([t.model_dump() for t in ts]).lower()
    private = Path(config_dir).resolve().parent / "local" / "topics.yaml"
    if private.is_file():  # 私有课题名只从本机 local/ 读取，不写进仓库文件
        for t in load_topics(private):
            assert t.name.lower() not in blob
