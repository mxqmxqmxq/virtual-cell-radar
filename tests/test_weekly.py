from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from aivc.config import load_config
from aivc.llm import LLMCache, LLMScorer
from aivc.models import LLMAnnotation, Paper
from aivc.storage import PaperStore
from aivc.topics import Topic
from aivc.weekly import (
    last_complete_week,
    parse_week,
    render_weekly,
    run_weekly,
    week_id,
    week_of,
)

TODAY = date(2026, 11, 14)
W44 = (date(2026, 10, 26), date(2026, 11, 1))


@pytest.fixture(autouse=True)
def _nokey(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def cfg(config_dir):
    return load_config(config_dir)


def paper(i, first_seen, *, published=None, score=None, kw=1.0, tasks=(), paradigms=(),
          newds=(), novelty="", title=None, circle="core"):
    p = Paper(
        id=f"arxiv:2610.{i:05d}", title=title or f"Paper {i}", source="arxiv",
        arxiv_id=f"2610.{i:05d}", published=published or first_seen, first_seen=first_seen,
        keyword_score=kw, circle=circle, url=f"https://x.org/{i}",
    )
    if score is not None:
        ann = LLMAnnotation(
            tasks=list(tasks), paradigms=list(paradigms), releases_new_dataset=bool(newds),
            new_dataset_names=list(newds), circle="core", relevance=int(score),
            novelty_zh=novelty, summary_zh="摘要", model="m", prompt_version="v1",
            scored_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        p = p.model_copy(update={"llm": ann, "llm_status": "scored", "final_score": score,
                                 "must_read": score >= 8})
    return p


def render(cfg, papers, span=W44, **kw):
    return render_weekly(papers, span[0], span[1], cfg, today=TODAY, **kw)


def test_week_helpers():
    assert week_id(date(2026, 11, 2)) == "2026-W45"
    assert parse_week("2026-W45") == (date(2026, 11, 2), date(2026, 11, 8))
    assert week_of(date(2026, 11, 5)) == (date(2026, 11, 2), date(2026, 11, 8))
    assert last_complete_week(date(2026, 11, 14)) == (date(2026, 11, 2), date(2026, 11, 8))
    # Sunday: previous week
    assert last_complete_week(date(2026, 11, 15)) == (date(2026, 11, 2), date(2026, 11, 8))
    # Monday
    assert last_complete_week(date(2026, 11, 9)) == (date(2026, 11, 2), date(2026, 11, 8))


def test_year_boundary():
    assert week_id(date(2027, 1, 1)) == "2026-W53"
    assert parse_week("2026-W53") == (date(2026, 12, 28), date(2027, 1, 3))
    assert last_complete_week(date(2027, 1, 5)) == (date(2026, 12, 28), date(2027, 1, 3))


@pytest.mark.parametrize("bad", ["2026-45", "2026-W5", "abc", "", "2026-W00", "2026-W54", "2025-W53"])
def test_parse_week_invalid(bad):
    with pytest.raises(ValueError, match="周"):
        parse_week(bad)


def test_empty_week(cfg):
    md = render(cfg, [paper(1, date(2026, 11, 3))])
    assert md.startswith("# AIVC 周报 · 2026-W44（2026-10-26 ~ 2026-11-01）\n")
    assert "本周无新增论文。" in md
    assert "## " not in md
    assert "first_seen" in md


def test_all_unscored(cfg):
    ps = [paper(i, date(2026, 10, 27), kw=float(i), title=f"Unscored {i}") for i in range(1, 4)]
    md = render(cfg, ps)
    assert "有 3 篇未打分" in md and "uv run python -m aivc score" in md
    assert "## 未打分论文（按关键词分排序）" in md
    assert md.index("Unscored 3") < md.index("Unscored 1")
    assert "本周暂无已打分论文" in md
    assert "本周无（或尚未打分）" in md
    assert "暂无分布数据" in md


def test_unscored_overflow(cfg):
    cfg.pipeline.weekly.max_unscored = 2
    ps = [paper(i, date(2026, 10, 27), kw=float(i)) for i in range(1, 6)]
    assert "另有 3 篇未列出" in render(cfg, ps)


def test_scored_week_with_comparison(cfg):
    prev = [paper(10, date(2026, 10, 20), score=7, tasks=["spatial"], paradigms=["llm_agent"])]
    cur = [
        paper(1, date(2026, 10, 27), score=9, tasks=["spatial", "drug_response"],
              paradigms=["world_model_jepa"], novelty="新意A", newds=["DS-X"], title="Alpha | pipe"),
        paper(2, date(2026, 10, 28), score=8.5, tasks=["spatial"], paradigms=["llm_agent"],
              novelty="新意B", title="Beta\nline"),
        paper(3, date(2026, 10, 29), kw=3, title="Gamma unscored"),
    ]
    md = render(cfg, prev + cur)
    assert md.index("### 1. Alpha | pipe") < md.index("### 2. Beta line")
    assert "新意：新意A" in md and "空间组学" in md
    assert "**DS-X**" in md
    assert f"必读（≥{cfg.llm.must_read_threshold:g} 分）：2 篇" in md
    # paradigm table: llm_agent 1 vs 1 -> 0 ; jepa 1 vs 0 -> +1
    assert "| 世界模型/JEPA | 1 | 0 | +1 |" in md
    assert "| LLM/智能体 | 1 | 1 | 0 |" in md
    assert "| 空间组学 | 2 | 1 | +1 |" in md
    assert "| 药物响应 | 1 | 0 | +1 |" in md
    # previous-week-only row appears with negative change
    md2 = render(cfg, prev + [cur[2]])
    assert "| 空间组学 | 0 | 1 | -1 |" in md2


def test_top_n(cfg):
    cfg.pipeline.weekly.top_n = 1
    ps = [paper(1, date(2026, 10, 27), score=9, title="A1"), paper(2, date(2026, 10, 27), score=8, title="B2")]
    md = render(cfg, ps)
    assert "### 1. A1" in md and "B2" not in md


def test_deterministic(cfg):
    ps = [paper(1, date(2026, 10, 27), score=9)]
    assert render(cfg, ps) == render(cfg, ps)


def test_in_progress_note(cfg):
    md = render_weekly([], date(2026, 11, 9), date(2026, 11, 15), cfg, today=TODAY)
    assert "本周尚未结束，数据截至 2026-11-14" in md


TA = Topic(id="a", name="课题甲", terms=["alpha"])
TB = Topic(id="b", name="课题乙", terms=["beta"])


def test_topic_groups(cfg):
    cfg.pipeline.weekly.max_per_topic = 1
    ps = [
        paper(1, date(2026, 10, 27), score=9, title="HA alpha"),
        paper(2, date(2026, 10, 27), kw=5, title="HB alpha"),
        paper(3, date(2026, 10, 27), kw=2, title="HC beta"),
    ]
    md = render(cfg, ps, topics=[TA, TB])
    sec = md[md.index("## 主题追踪"):]
    assert sec.index("### 课题甲") < sec.index("### 课题乙")
    assert "HA alpha" in sec and "HC beta" in sec and "HB alpha" not in sec
    assert "另有 1 篇。" in sec
    assert "个人课题" not in md
    assert "HA alpha 【相关课题：课题甲】" in md  # Top entry suffix


def test_topics_no_hit(cfg):
    md = render(cfg, [paper(1, date(2026, 10, 27), score=9, title="Plain")], topics=[TA])
    assert "## 主题追踪" in md and "本周无命中主题的论文。" in md


def test_no_topics_no_section(cfg):
    ps = [paper(1, date(2026, 10, 27), score=9, title="HA alpha")]
    for topics in (None, []):
        md = render(cfg, ps, topics=topics)
        assert "主题追踪" not in md and "相关课题" not in md


def test_run_weekly_topics_and_papers(cfg, tmp_path):
    ps = [paper(1, date(2026, 10, 27), score=9, title="HA alpha")]
    res = run_weekly(cfg, data_dir=tmp_path / "data", reports_dir=tmp_path / "reports",
                     week="2026-W44", today=TODAY, topics=[TA], papers=ps)
    text = res.path.read_text(encoding="utf-8")
    assert res.n_total == 1 and "### 课题甲" in text


def test_cross_month_run(cfg, tmp_path):
    data = tmp_path / "data"
    PaperStore(data).upsert([
        paper(1, date(2026, 10, 27), published=date(2026, 10, 27), score=9, title="Oct paper"),
        paper(2, date(2026, 11, 1), published=date(2026, 11, 1), kw=2, title="Nov paper"),
        paper(3, date(2026, 11, 2), published=date(2026, 11, 2), kw=2, title="Next week"),
    ])
    assert len(list((data / "papers").glob("*.jsonl"))) == 2
    before = sorted((p.name, p.stat().st_mtime_ns) for p in (data / "papers").glob("*"))
    res = run_weekly(cfg, data_dir=data, reports_dir=tmp_path / "reports",
                     week="2026-W44", today=TODAY)
    assert res.path == tmp_path / "reports" / "weekly" / "2026-W44.md"
    assert res.n_total == 2 and res.n_scored == 1 and res.n_unscored == 1
    text = res.path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "# AIVC 周报 · 2026-W44（2026-10-26 ~ 2026-11-01）"
    assert "Oct paper" in text and "Nov paper" in text and "Next week" not in text
    after = sorted((p.name, p.stat().st_mtime_ns) for p in (data / "papers").glob("*"))
    assert before == after


def test_run_default_and_end(cfg, tmp_path):
    data = tmp_path / "data"
    r = run_weekly(cfg, data_dir=data, reports_dir=tmp_path / "r", today=TODAY)
    assert r.week_id == "2026-W45" and r.n_total == 0
    assert "本周无新增论文。" in r.path.read_text(encoding="utf-8")
    r2 = run_weekly(cfg, data_dir=data, reports_dir=tmp_path / "r", end=date(2026, 11, 14), today=TODAY)
    assert r2.week_id == "2026-W46"
    assert "本周尚未结束" in r2.path.read_text(encoding="utf-8")


def test_week_and_end_exclusive(cfg, tmp_path):
    with pytest.raises(ValueError):
        run_weekly(cfg, data_dir=tmp_path, reports_dir=tmp_path, week="2026-W44", end=TODAY)


class FakeClient:
    def __init__(self, inp=None):
        self.calls = []
        self.inp = {"overview_zh": "本周综述文本。"} if inp is None else inp
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.inp)])


def _seed(tmp_path):
    data = tmp_path / "data"
    PaperStore(data).upsert([paper(1, date(2026, 10, 27), score=9, novelty="新意A")])
    return data


def test_overview_off_no_call(cfg, tmp_path):
    data = _seed(tmp_path)
    fc = FakeClient()
    scorer = LLMScorer(cfg, LLMCache(tmp_path / "c.jsonl"), client=fc)
    r = run_weekly(cfg, data_dir=data, reports_dir=tmp_path / "r", week="2026-W44", scorer=scorer)
    assert fc.calls == [] and not r.overview_written
    assert "## 本周综述" not in r.path.read_text(encoding="utf-8")


def test_overview_on(cfg, tmp_path):
    cfg.pipeline.weekly.llm_overview = True
    cfg.pipeline.weekly.overview_model = "model-x"
    data = _seed(tmp_path)
    fc = FakeClient()
    scorer = LLMScorer(cfg, LLMCache(tmp_path / "c.jsonl"), client=fc)
    r = run_weekly(cfg, data_dir=data, reports_dir=tmp_path / "r", week="2026-W44", scorer=scorer)
    assert r.overview_written and len(fc.calls) == 1
    assert fc.calls[0]["model"] == "model-x"
    assert fc.calls[0]["tool_choice"]["name"] == "record_weekly_overview"
    assert "## 本周综述\n\n本周综述文本。" in r.path.read_text(encoding="utf-8")


def test_overview_empty_response_skipped(cfg, tmp_path):
    cfg.pipeline.weekly.llm_overview = True
    data = _seed(tmp_path)
    scorer = LLMScorer(cfg, LLMCache(tmp_path / "c.jsonl"), client=FakeClient({"overview_zh": " "}))
    r = run_weekly(cfg, data_dir=data, reports_dir=tmp_path / "r", week="2026-W44", scorer=scorer)
    assert not r.overview_written and r.notes and r.path.exists()


def test_overview_no_backend(cfg, tmp_path):
    cfg.pipeline.weekly.llm_overview = True
    data = _seed(tmp_path)
    r = run_weekly(cfg, data_dir=data, reports_dir=tmp_path / "r", week="2026-W44")
    assert not r.overview_written and any("后端" in n for n in r.notes)
    assert r.path.exists()


def test_complete_structured_error_returns_none(cfg, tmp_path):
    class Boom:
        messages = SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(RuntimeError("x")))

    s = LLMScorer(cfg, LLMCache(tmp_path / "c.jsonl"), client=Boom())
    assert s.complete_structured(system="s", user="u", tool={"name": "t"}) is None


# ---------------------------------------------------------------- 行业动态
def _news(i, **kw):
    from aivc.models import NewsItem, make_news_id
    d = dict(id=make_news_id(f"https://x.example/{i}"), title=f"虚构新闻 {i}", url=f"https://x.example/{i}",
             source="Fake Media", published=date(2026, 10, 28), first_seen=date(2026, 10, 28),
             llm_status="scored", event_type="funding", relevance=7, companies=["Recursion"],
             summary_zh="摘要。", significance_zh="意义。", amount_usd=3e7)
    d.update(kw)
    return NewsItem(**d)


def test_weekly_without_news_unchanged(cfg):
    base = render(cfg, [paper(1, W44[0], score=7)])
    assert "行业动态" not in base
    assert render(cfg, [paper(1, W44[0], score=7)], news=[]) == base


def test_weekly_news_section_position(cfg):
    out = render(cfg, [paper(1, W44[0], score=7, newds=("DS1",))], news=[_news(1)])
    assert "## 行业动态" in out and "### 融资汇总" in out
    assert out.index("## 新数据集 / 基准") < out.index("## 行业动态") < out.index("## 范式分布")


def test_weekly_news_when_no_papers(cfg):
    out = render(cfg, [], news=[_news(1)])
    assert "本周无新增论文。" in out and "## 行业动态" in out and "虚构新闻 1" in out


def test_run_weekly_reads_news_store(cfg, tmp_path):
    from aivc.news import NewsStore
    NewsStore(tmp_path).upsert([_news(1)])
    r = run_weekly(cfg, data_dir=tmp_path, reports_dir=tmp_path / "r", week="2026-W44", today=TODAY)
    assert "## 行业动态" in r.path.read_text(encoding="utf-8")
    r2 = run_weekly(cfg, data_dir=tmp_path / "empty", reports_dir=tmp_path / "r2", week="2026-W44", today=TODAY)
    assert "行业动态" not in r2.path.read_text(encoding="utf-8")


def test_weekly_v4_fields(cfg):
    p = paper(1, date(2026, 10, 27), score=9, novelty="新意A")
    p = p.model_copy(update={
        "published_venue": "Nature", "citation_count": 5, "weights_url": "https://zenodo.org/records/1",
        "llm": p.llm.model_copy(update={"novelty_score": 8, "rigor_score": 7, "impact_score": 9,
                                        "why_it_matters_zh": "理由", "limitations_zh": "局限X"}),
    })
    md = render(cfg, [p])
    assert "正式发表：直接抓到的期刊论文 0 篇，由预印本晋升 1 篇" in md
    assert "【已发表：Nature】 · 引用 5" in md
    assert "- 四维：新颖 8/严谨 7/影响 9" in md and "- 为何重要：理由" in md and "- 局限：局限X" in md
    assert "[权重](https://zenodo.org/records/1)" in md
    plain = render(cfg, [paper(2, date(2026, 10, 27), score=9)])
    assert "正式发表" not in plain and "四维" not in plain


def test_weekly_direct_and_promoted_and_zero_sources(cfg):
    a = paper(1, date(2026, 10, 27), score=9).model_copy(update={"published_venue": "Nature"})
    b = paper(2, date(2026, 10, 27), score=9).model_copy(update={"source": "openalex", "published_venue": "Cell"})
    cfg.sources.hf_papers.enabled = True
    md = render(cfg, [a, b])
    assert "- 正式发表：直接抓到的期刊论文 1 篇，由预印本晋升 1 篇" in md
    assert "hf_papers 0" in md
