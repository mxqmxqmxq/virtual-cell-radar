from datetime import date, datetime

from aivc.models import LLMAnnotation, Paper
from aivc.report import render_daily, write_daily
from aivc.topics import Topic

TOPIC_A = Topic(id="a", name="课题甲", terms=["must title"])
TOPIC_B = Topic(id="b", name="课题乙", terms=["another must", "must title"])
RUN = date(2026, 11, 14)
WINDOW = (date(2026, 11, 11), RUN)


def make_llm(**overrides) -> LLMAnnotation:
    base = dict(
        tasks=["perturbation_response_prediction", "drug_response"],
        paradigms=["flow_matching_diffusion_ot"],
        datasets_used=["Tahoe-100M", "LINCS L1000"],
        releases_new_dataset=True,
        new_dataset_names=["CrossPerturb"],
        circle="core",
        relevance=9,
        novelty_zh="新意句",
        summary_zh="摘要句",
        model="claude-haiku-5-5",
        prompt_version="v1",
        scored_at=datetime(2026, 11, 14, 8, 0),
    )
    base.update(overrides)
    return LLMAnnotation(**base)


def make_paper(pid: str, title: str, **overrides) -> Paper:
    base = dict(
        id=pid,
        title=title,
        source="arxiv",
        published=date(2026, 11, 8),
        first_seen=RUN,
        url=f"https://arxiv.org/abs/{pid.split(':')[1]}",
        pdf_url=f"https://arxiv.org/pdf/{pid.split(':')[1]}",
        authors=["A", "B", "C", "D"],
    )
    base.update(overrides)
    return Paper(**base)


def fixture_papers() -> list[Paper]:
    # Scored, must_read, has code link, 4 authors.
    must_title = make_paper(
        "arxiv:1",
        "Must Title",
        final_score=9.0,
        must_read=True,
        circle="core",
        keyword_score=5.0,
        llm_status="scored",
        llm=make_llm(),
        code_url="https://github.com/x/y",
    )
    # Scored, must_read, same final score but higher keyword score; no authors,
    # no date (falls back to first_seen), no url at all, empty lists.
    another_must = make_paper(
        "arxiv:2",
        "Another Must",
        published=None,
        authors=[],
        url=None,
        pdf_url=None,
        final_score=9.0,
        must_read=True,
        circle="core",
        keyword_score=7.0,
        llm_status="scored",
        llm=make_llm(
            tasks=[],
            paradigms=[],
            datasets_used=[],
            releases_new_dataset=False,
            new_dataset_names=[],
        ),
    )
    # Scored, not must_read; paper.circle core but llm.circle peripheral wins.
    other_scored = make_paper(
        "arxiv:3",
        "Other Scored",
        source="biorxiv",
        published=date(2026, 11, 7),
        authors=["X"],
        url="https://www.biorxiv.org/content/x",
        pdf_url=None,
        final_score=6.0,
        must_read=False,
        circle="core",
        keyword_score=4.0,
        llm_status="scored",
        llm=make_llm(circle="peripheral", releases_new_dataset=False, new_dataset_names=[]),
    )
    unscored = make_paper(
        "arxiv:4",
        "Unscored Paper",
        circle="peripheral",
        keyword_score=3.5,
        matched_keywords=["virtual cell", "perturbation"],
        url="https://arxiv.org/abs/4",
        pdf_url=None,
    )
    lower_unscored = make_paper(
        "arxiv:5",
        "Lower Unscored",
        circle="none",
        keyword_score=1.0,
        url=None,
        pdf_url=None,
    )
    return [other_scored, unscored, lower_unscored, another_must, must_title]


def test_section_order_and_numbering():
    out = render_daily(fixture_papers(), RUN)
    i_must = out.index("## 值得精读")
    i_other = out.index("## 其他已打分")
    i_unscored = out.index("## 未打分（按关键词分排序）")
    assert i_must < i_other < i_unscored
    assert "### 1. Another Must" in out
    assert "### 2. Must Title" in out
    assert "### 3. Other Scored" in out


def test_scored_sort_tiebreak_by_keyword_score():
    out = render_daily(fixture_papers(), RUN)
    assert out.index("### 1. Another Must") < out.index("### 2. Must Title")


def test_unscored_sorted_by_keyword_score():
    out = render_daily(fixture_papers(), RUN)
    assert out.index("**Unscored Paper**") < out.index("**Lower Unscored**")


def test_topic_suffix_in_heading():
    out = render_daily(fixture_papers(), RUN, topics=[TOPIC_A, TOPIC_B])
    assert "### 2. Must Title 【相关课题：课题甲, 课题乙】" in out
    assert "### 1. Another Must 【相关课题：课题乙】" in out
    assert "### 3. Other Scored\n" in out


def test_no_topics_no_suffix():
    for topics in (None, []):
        out = render_daily(fixture_papers(), RUN, topics=topics)
        assert "相关课题" not in out


def test_topic_suffix_on_unscored_line():
    t = Topic(id="u", name="课题甲", terms=["lower unscored"])
    out = render_daily(fixture_papers(), RUN, topics=[t])
    assert "**Lower Unscored 【相关课题：课题甲】**" in out


def test_must_read_threshold_text():
    assert "## 值得精读（≥9 分）" in render_daily(fixture_papers(), RUN, must_read_threshold=9.0)
    assert "## 值得精读（≥7.5 分）" in render_daily(fixture_papers(), RUN, must_read_threshold=7.5)
    assert "## 值得精读\n" in render_daily(fixture_papers(), RUN)


def test_score_line_and_circle_from_llm():
    out = render_daily(fixture_papers(), RUN)
    assert "- 分数：9.0（核心圈）｜ 来源：arxiv ｜ 日期：2026-11-08" in out
    # date falls back to first_seen when published is None
    assert "- 分数：9.0（核心圈）｜ 来源：arxiv ｜ 日期：2026-11-14" in out
    # llm.circle (peripheral) wins over paper.circle (core)
    assert "- 分数：6.0（外围圈）｜ 来源：biorxiv ｜ 日期：2026-11-07" in out


def test_authors_formatting():
    out = render_daily(fixture_papers(), RUN)
    assert "- 作者：A, B, C 等（共 4 人）" in out
    assert "- 作者：X" in out
    assert "- 作者：（未提供）" in out


def test_tasks_paradigms_datasets_and_new_dataset_flag():
    out = render_daily(fixture_papers(), RUN)
    assert (
        "- 任务：perturbation_response_prediction, drug_response ｜ 范式：flow_matching_diffusion_ot"
        in out
    )
    assert "- 数据：Tahoe-100M, LINCS L1000 ｜ **新数据集/基准**：是（CrossPerturb）" in out
    assert "- 任务：— ｜ 范式：—" in out
    assert "- 数据：— ｜ **新数据集/基准**：否" in out


def test_new_dataset_true_without_names_writes_plain_yes():
    p = make_paper(
        "arxiv:9",
        "Plain Yes",
        final_score=8.5,
        must_read=True,
        llm_status="scored",
        llm=make_llm(releases_new_dataset=True, new_dataset_names=[]),
    )
    out = render_daily([p], RUN)
    assert "**新数据集/基准**：是\n" in out


def test_novelty_and_summary_lines():
    out = render_daily(fixture_papers(), RUN)
    assert "- 新意：新意句" in out
    assert "- 摘要：摘要句" in out


def test_links_line_omitted_when_all_empty_and_full_when_present():
    out = render_daily(fixture_papers(), RUN)
    assert (
        "- 链接：[原文](https://arxiv.org/abs/1) · [PDF](https://arxiv.org/pdf/1) "
        "· [代码](https://github.com/x/y)"
    ) in out
    assert "- 链接：[原文](https://www.biorxiv.org/content/x)" in out
    # Only Must Title and Other Scored have a scored-entry link line.
    assert out.count("- 链接：") == 2


def test_unscored_line_format():
    out = render_daily(fixture_papers(), RUN)
    assert (
        "- **Unscored Paper** — arxiv · 2026-11-08 · 关键词分 3.5（外围圈）"
        "· 命中：virtual cell, perturbation · [原文](https://arxiv.org/abs/4)"
    ) in out
    assert "- **Lower Unscored** — arxiv · 2026-11-08 · 关键词分 1.0（范围外）· 命中：—" in out


def test_stats_line_with_and_without_window():
    papers = fixture_papers()
    out = render_daily(papers, RUN, window=WINDOW, notes=["bioRxiv 限流，部分结果缺失"])
    assert (
        "> 抓取窗口：2026-11-11 ~ 2026-11-14 ｜ 新增 5 篇（已打分 3，未打分 2）"
        "｜ 来源：arxiv 4，biorxiv 1"
    ) in out
    assert "> 注意：bioRxiv 限流，部分结果缺失" in out

    out_nowin = render_daily(papers, RUN)
    assert "抓取窗口" not in out_nowin
    assert "> 新增 5 篇（已打分 3，未打分 2）｜ 来源：arxiv 4，biorxiv 1" in out_nowin


def test_default_and_custom_title():
    out = render_daily([], RUN)
    assert out.splitlines()[0] == "# AIVC 每日论文 · 2026-11-14"
    out2 = render_daily([], RUN, title="Custom\nTitle")
    assert out2.splitlines()[0] == "# Custom Title"


def test_empty_output_exact():
    assert render_daily([], RUN) == "# AIVC 每日论文 · 2026-11-14\n\n> 今日无新增论文。\n"


def test_empty_with_window_keeps_window_line():
    out = render_daily([], RUN, window=WINDOW)
    assert "> 抓取窗口：2026-11-11 ~ 2026-11-14" in out
    assert "> 今日无新增论文。" in out
    assert "## " not in out


def test_empty_sections_are_omitted():
    only_unscored = [make_paper("arxiv:7", "Solo", keyword_score=2.0, circle="core")]
    out = render_daily(only_unscored, RUN)
    assert "## 值得精读" not in out
    assert "## 其他已打分" not in out
    assert "## 未打分（按关键词分排序）" in out


def test_max_items_truncation():
    out = render_daily(fixture_papers(), RUN, max_items=2)
    assert out.count("### ") == 2
    assert "### 2. Must Title" in out
    assert "### 3." not in out
    assert "**Unscored Paper**" not in out
    assert "> 另有 3 篇未列出。" in out


def test_write_daily_writes_expected_path_and_content(tmp_path):
    papers = fixture_papers()
    path = write_daily(papers, RUN, tmp_path, window=WINDOW)
    assert path == tmp_path / "daily" / "2026-11-14.md"
    assert path.read_text(encoding="utf-8") == render_daily(papers, RUN, window=WINDOW)


def test_write_daily_custom_subdir_and_filename_overwrites(tmp_path):
    path = write_daily([], RUN, tmp_path, subdir="x", filename="custom.md")
    assert path == tmp_path / "x" / "custom.md"
    path = write_daily(fixture_papers(), RUN, tmp_path, subdir="x", filename="custom.md")
    assert path.read_text(encoding="utf-8") == render_daily(fixture_papers(), RUN)


def test_write_daily_empty_rerun_keeps_previous_report(tmp_path):
    from datetime import date
    from aivc.report import write_daily
    d = date(2026, 1, 2)
    p = tmp_path / "daily" / "2026-01-02.md"
    p.parent.mkdir()
    p.write_text("# earlier\n\n- **paper A**\n", encoding="utf-8")
    out = write_daily([], d, tmp_path)
    assert out == p and "paper A" in p.read_text(encoding="utf-8")


def test_pinned_news_section_and_default_unchanged():
    from aivc.models import NewsItem, make_news_id
    papers = fixture_papers()
    base = render_daily(papers, RUN)
    assert render_daily(papers, RUN, pinned_news=None) == base
    assert render_daily(papers, RUN, pinned_news=[]) == base
    it = NewsItem(id=make_news_id("https://x.example/1"), title="虚构重大事件", url="https://x.example/1",
                  source="Fake Media", published=RUN, first_seen=RUN, llm_status="scored",
                  event_type="funding", companies=["Recursion"], amount_usd=2e8, significance_zh="虚构意义",
                  major=True)
    out = render_daily(papers, RUN, pinned_news=[it])
    assert "## 重大行业事件" in out and "**[重大] 虚构重大事件**" in out
    heads = [ln for ln in out.splitlines() if ln.startswith("## ")]
    assert heads[0] == "## 重大行业事件"
