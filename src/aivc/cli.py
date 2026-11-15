"""Command line entry: python -m aivc {update,news,backfill,seed,reindex,score,weekly,build-site,serve,...}.

`resources` 管理知识资源（list/check/refresh）；`news` 抓取并处理行业新闻（--days/--no-llm/--limit/--no-site）；`news-rescore` 只重打库内已有条目（--ids/--include-unscored/--max-requests/--dry-run）；`update` 默认也会先跑新闻（--no-news 关闭）。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from . import news_pipeline, pipeline, publiccheck, resources as resources_mod, site, weekly
from .config import load_config
from .fetchers.github import GitHubClient, build_github_client
from .news import NewsStore
from .pipeline import PipelineError, RunSummary
from .resources import KIND_ORDER
from .storage import PaperStore, load_overlay
from .topics import load_topics, merge_topics, report_topics

log = logging.getLogger("aivc.cli")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aivc", description="虚拟细胞雷达（Virtual Cell Radar）流水线")
    p.add_argument("--config-dir", type=Path,
                   default=Path(os.environ.get("AIVC_CONFIG_DIR") or "config"),
                   help="配置目录（默认 AIVC_CONFIG_DIR 或 ./config）")
    p.add_argument("--data-dir", type=Path,
                   default=Path(os.environ.get("AIVC_DATA_DIR") or "data"),
                   help="数据目录（默认 AIVC_DATA_DIR 或 ./data）")
    p.add_argument("--reports-dir", type=Path, default=Path("reports"), help="报告目录（默认 ./reports）")
    p.add_argument("--site-dir", type=Path,
                   default=Path(os.environ.get("AIVC_SITE_DIR") or "site"),
                   help="静态网站输出目录（默认 AIVC_SITE_DIR 或 ./site）")
    p.add_argument("--local", action="store_true",
                   default=os.environ.get("AIVC_LOCAL", "").strip().lower() in ("1", "true", "yes"),
                   help="本机私有视图：叠加 local 目录的私有课题与论文，输出到 <local-dir>/reports 与 site"
                        "（也可设环境变量 AIVC_LOCAL=1）")
    p.add_argument("--local-dir", type=Path,
                   default=Path(os.environ.get("AIVC_LOCAL_DIR") or "local"),
                   help="私有目录（默认 AIVC_LOCAL_DIR 或 ./local）")
    p.add_argument("-v", "--verbose", action="store_true", help="输出 DEBUG 日志")
    sub = p.add_subparsers(dest="command", required=True)

    u = sub.add_parser("update", help="抓取最近几天的论文并生成日报")
    u.add_argument("--days", type=int, default=None, help="回看天数（默认取 config/pipeline.yaml）")
    u.add_argument("--no-llm", action="store_true", help="跳过 LLM 打分")
    u.add_argument("--no-site", action="store_true", help="完成后不自动重建静态网站")
    u.add_argument("--refresh-resources", action="store_true",
                   help="论文更新后顺带刷新知识资源的 GitHub 指标（默认关；也可设 pipeline.yaml 的 resources.run_with_update）")
    u.add_argument("--no-news", action="store_true", help="不顺带抓取行业新闻（默认由 pipeline.yaml 的 news.run_with_update 控制）")

    nw = sub.add_parser("news", help="抓取并处理行业新闻（融资/合作/并购/模型发布等）")
    nw.add_argument("--days", type=int, default=None, help="回看天数（含今天；默认取 pipeline.yaml 的 news.default_days）")
    nw.add_argument("--no-llm", action="store_true", help="跳过 LLM 抽取（新闻入库但不打分）")
    nw.add_argument("--limit", type=int, default=None, help="本次最多让 N 条候选进入 LLM 与入库，其余不处理")
    nw.add_argument("--rescore-stale", action="store_true",
                    help="库内旧提示词版本的新闻若本次抓取又出现，用新片段按当前版本重打（独立预算 news.rescore_stale_max_calls）")
    nw.add_argument("--no-site", action="store_true", help="完成后不自动重建静态网站")

    rs = sub.add_parser("news-rescore", help="只重打库内旧提示词版本的新闻（不入库新条目；抓取只为找回片段）")
    rs.add_argument("--days", type=int, default=None, help="找回片段的抓取窗口天数（默认：从最早目标条目日期到今天，最多 90 天）")
    rs.add_argument("--no-fetch", action="store_true", help="不联网抓取，用库内旧摘要（或仅标题）作为片段")
    rs.add_argument("--ids", default=None, help="只重打这些条目 id（逗号分隔）")
    rs.add_argument("--include-unscored", action="store_true", help="同时处理有日期但从未打分的条目")
    rs.add_argument("--max-requests", type=int, default=None,
                    help="LLM 真实请求数硬上限（含重试；默认取 news.rescore_stale_max_calls）")
    rs.add_argument("--dry-run", action="store_true", help="只列出目标条目与片段来源，不调用 LLM、不写库")
    rs.add_argument("--no-site", action="store_true", help="完成后不自动重建静态网站")

    b = sub.add_parser("backfill", help="回填历史数据")
    b.add_argument("--days", type=int, required=True, help="回填天数")
    b.add_argument("--no-llm", action="store_true", help="跳过 LLM 打分")

    s = sub.add_parser("seed", help="导入种子 PDF 与 ICLR 摘要汇总")
    s.add_argument("--source-dir", type=Path, default=None,
                   help="种子目录（默认 AIVC_SEED_DIR 或 config/sources.yaml 的 seed.source_dir）")
    s.add_argument("--iclr-md", type=Path, default=None, help="ICLR 摘要汇总 markdown")
    s.add_argument("--no-llm", action="store_true", help="跳过 LLM 打分")

    r = sub.add_parser("reindex", help="按当前关键词配置重算库内条目的圈层与关键词分")
    r.add_argument("--prune", action="store_true", help="删除重算后不再相关的抓取条目（种子条目保留）")

    sc = sub.add_parser("score", help="对库内未打分论文补打分")
    sc.add_argument("--limit", type=int, default=None, help="最多处理篇数")

    w = sub.add_parser("weekly", help="生成周报（按 first_seen 划分自然周，周一到周日）")
    g = w.add_mutually_exclusive_group()
    g.add_argument("--week", default=None, help="指定周，格式 YYYY-Www，如 2026-W45")
    g.add_argument("--end", default=None, help="指定日期 YYYY-MM-DD，生成包含该日的自然周")
    w.add_argument("--no-site", action="store_true", help="完成后不自动重建静态网站")

    sub.add_parser("build-site", help="把数据导出为本地静态网站（site/）")

    rf = sub.add_parser("refresh-openreview",
                        help="用 OpenReview 官方 API 核验并覆盖公开库里的 ICLR 条目（需联网；取不回的移到 local）")
    rf.add_argument("--dry-run", action="store_true", help="只打印每条将发生的动作与汇总，不写任何文件")
    rf.add_argument("--limit", type=int, default=None, help="最多处理篇数")

    rs = sub.add_parser("resources", help="知识资源板块：list / check / refresh / discover / accept / archive")
    rsub = rs.add_subparsers(dest="resources_command", required=True)
    rl = rsub.add_parser("list", help="按类型分组列出资源及 star、最近推送、是否维护")
    rl.add_argument("--status", choices=["accepted", "candidate", "archived"], default=None, help="只列某状态")
    rl.add_argument("--kind", choices=list(KIND_ORDER), default=None, help="只列某类型")
    rsub.add_parser("check", help="离线校验资源文件（id/url 重复、kind 与文件不符、taxonomy id 非法等；有问题退出码 1）")
    rd = rsub.add_parser("discover", help="从论文库与新闻库提取候选仓库/数据集，写入 candidates.yaml（不联网，不进精选）")
    rd.add_argument("--dry-run", action="store_true", help="只打印将新增的候选，不写盘")
    ra = rsub.add_parser("accept", help="把候选移入精选清单并置为 accepted")
    ra.add_argument("id")
    ra.add_argument("--kind", choices=list(KIND_ORDER), default=None, help="目标类型（默认沿用候选的 kind）")
    ra.add_argument("--url", default=None, help="资源链接（数据集候选没有 url，必须提供）")
    ra.add_argument("--name", default=None, help="覆盖名称")
    ra.add_argument("--description", default=None, help="中文描述（自己的话、可核实的事实）")
    rv = rsub.add_parser("archive", help="归档：候选不再被重新发现；精选条目在网站不再展示")
    rv.add_argument("id")
    rr = rsub.add_parser("refresh", help="用 GitHub API 刷新精选仓库的 star、最近推送、归档、许可证、主语言（需联网）")
    rr.add_argument("--force", action="store_true", help="忽略 refresh_min_interval_days，重新抓取所有仓库")
    rr.add_argument("--limit", type=int, default=None, help="本次最多请求 N 次")

    sub.add_parser("check-public", help="发布前自查：检查公开库、报告、网站与仓库是否含私有信息（只读）")

    sv = sub.add_parser("serve", help="在 localhost 预览静态网站")
    sv.add_argument("--port", type=int, default=None, help="端口（默认取 config/pipeline.yaml，即 8000）")
    return p


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.INFO if verbose else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def print_summary(s: RunSummary, out=None) -> None:
    out = out or sys.stdout
    w = lambda m: print(m, file=out)  # noqa: E731
    if s.window:
        w(f"时间窗口：{s.window[0]} ~ {s.window[1]}")
    if s.fetched or s.fetch_errors:
        parts = [f"{k}={v}" for k, v in s.fetched.items()]
        w("各源抓取数：" + (", ".join(parts) or "无"))
        for k, e in s.fetch_errors.items():
            w(f"  抓取失败 {k}: {e}")
    w(f"预筛后保留：{s.kept_after_filter}")
    w(f"新增 {s.n_new} / 更新 {s.n_updated} / 重复 {s.n_duplicates}")
    if s.llm is not None:
        st = s.llm
        w(f"LLM：打分 {st.scored}（缓存命中 {st.cache_hits}，API 调用 {st.api_calls}），"
          f"错误 {st.errors}，无 key 跳过 {st.skipped_no_key}，无摘要跳过 {st.skipped_no_abstract}，"
          f"超预算跳过 {st.skipped_budget}")
    else:
        w("LLM：未执行")
    for n in s.notes:
        w(f"备注：{n}")
    if s.report_path:
        w(f"报告：{s.report_path}")


def print_news_summary(s: "news_pipeline.NewsRunSummary", out=None) -> None:
    out = out or sys.stdout
    w = lambda m: print(m, file=out)  # noqa: E731
    if s.window:
        w(f"新闻时间窗口：{s.window[0]} ~ {s.window[1]}")
    if s.per_feed or s.fetch_errors:
        w("各源条数：" + (", ".join(f"{k}={v}" for k, v in s.per_feed.items()) or "无"))
        for k, e in s.fetch_errors.items():
            w(f"  抓取失败 {k}: {e}")
    w(f"HTTP 请求数：{s.requests}")
    w(f"抓取 {s.n_fetched} / 预筛后 {s.n_candidates} / 重复 {s.n_duplicates}")
    w(f"新增 {s.n_new} / 更新 {s.n_updated} / 事件合并 {s.n_merged} / 因 --limit 未处理 {s.n_skipped_limit}")
    if s.llm is not None:
        st = s.llm
        w(f"LLM：打分 {st.scored}（缓存命中 {st.cache_hits}，API 调用 {st.api_calls}），错误 {st.errors}，"
          f"无后端跳过 {st.skipped_no_backend}，超预算跳过 {st.skipped_budget}")
    else:
        w("LLM：未执行")
    if s.n_rescored or s.n_rescore_skipped_budget or s.n_rescore_failed:
        w(f"重打（旧提示词版本）：成功 {s.n_rescored}，失败保留旧分 {s.n_rescore_failed}，"
          f"因预算跳过 {s.n_rescore_skipped_budget}")
    for it in s.major:
        w(f"重大事件：{it.title}")
    for n in s.notes:
        w(f"备注：{n}")


def _news_line(s: "news_pipeline.NewsRunSummary") -> str:
    return (f"新闻：新增 {s.n_new} / 更新 {s.n_updated} / 重大事件 {len(s.major)}"
            f"（抓取 {s.n_fetched}，预筛后 {s.n_candidates}）")


def _run_news(cfg, args, data_dir, reports_dir) -> int:
    s = news_pipeline.run_news(cfg, data_dir=data_dir, days=args.days,
                               use_llm=not args.no_llm, limit=args.limit,
                               rescore_stale=args.rescore_stale)
    print_news_summary(s)
    if s.all_feeds_failed:
        print("错误：所有启用的新闻源均抓取失败", file=sys.stderr)
        return 1
    _auto_build_site(cfg, args, data_dir, reports_dir)
    return 0


def print_rescore_summary(s: "news_pipeline.RescoreSummary", out=None) -> None:
    out = out or sys.stdout
    w = lambda m: print(m, file=out)  # noqa: E731
    if s.window:
        w(f"片段抓取窗口：{s.window[0]} ~ {s.window[1]}")
    w(f"目标条目：{s.n_targets}（片段来源：重新抓到 {s.n_refetched}，库内旧摘要 {s.n_stored_summary}，"
      f"仅标题 {s.n_title_only}）")
    if s.dry_run:
        w("dry-run：未调用 LLM，未写库")
    else:
        w(f"重打成功 {s.n_rescored}，失败保留旧分 {s.n_failed}，因预算跳过 {s.n_skipped_budget}，缓存命中 {s.cache_hits}")
    w(f"LLM 真实请求数：{s.requests}")
    for id_, title, old, new, how in s.rows:
        o = "-" if old is None else old
        n = "-" if new is None else new
        w(f"  {id_}  {o} → {n}  [{how}]  {title}")
    for n in s.notes:
        w(f"备注：{n}")


def _run_news_rescore(cfg, args, data_dir, reports_dir) -> int:
    ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids else None
    s = news_pipeline.rescore_stored(cfg, data_dir=data_dir, days=args.days, fetch=not args.no_fetch,
                                     ids=ids, include_unscored=args.include_unscored,
                                     max_requests=args.max_requests, dry_run=args.dry_run)
    print_rescore_summary(s)
    if not args.dry_run:
        _auto_build_site(cfg, args, data_dir, reports_dir)
    return 0


def _local_topics(cfg, args):
    return merge_topics(list(cfg.topics.topics), load_topics(Path(args.local_dir) / "topics.yaml"))


def _site_kwargs(cfg, args, data_dir) -> dict:
    """带 --local 时给 build_site 的叠加参数；不带时为空（只读公开库与公开课题）。"""
    if not args.local:
        return {}
    return {"papers": load_overlay(data_dir, Path(args.local_dir) / "data"),
            "topics": _local_topics(cfg, args), "include_local": True}


def _auto_build_site(cfg, args, data_dir, reports_dir) -> None:
    """update/weekly 成功后按配置自动建站；失败只警告，不影响退出码。"""
    if getattr(args, "no_site", False) or not cfg.pipeline.site.auto_build:
        return
    try:
        site.build_site(cfg, data_dir=data_dir, reports_dir=reports_dir, site_dir=args.site_dir,
                        top_used=cfg.pipeline.site.top_datasets_used, **_site_kwargs(cfg, args, data_dir))
    except Exception as e:  # noqa: BLE001
        print(f"警告：自动建站失败：{e}", file=sys.stderr)


def _run_weekly(cfg, args, data_dir, reports_dir) -> int:
    end = None
    if args.end is not None:
        try:
            end = date.fromisoformat(args.end)
        except ValueError:
            raise ValueError(f"--end 日期格式应为 YYYY-MM-DD：{args.end}") from None
    extra = {}
    if args.local:
        extra = {"papers": load_overlay(data_dir, Path(args.local_dir) / "data"),
                 "topics": report_topics(_local_topics(cfg, args))}
    r = weekly.run_weekly(cfg, data_dir=data_dir, reports_dir=reports_dir, week=args.week, end=end, **extra)
    print(f"周：{r.week_id}（{r.start} ~ {r.end}，按 first_seen 划分）")
    print(f"新增 {r.n_total} 篇 / 已打分 {r.n_scored} / 未打分 {r.n_unscored}")
    if r.n_unscored:
        print(f"提示：有 {r.n_unscored} 篇未打分，运行 `python -m aivc score` 补打分后可重新生成周报")
    for n in r.notes:
        print(f"备注：{n}")
    print(f"报告：{r.path}")
    _auto_build_site(cfg, args, data_dir, reports_dir)
    return 0


def _run_build_site(cfg, args, data_dir, reports_dir) -> int:
    r = site.build_site(cfg, data_dir=data_dir, reports_dir=reports_dir, site_dir=args.site_dir,
                        top_used=cfg.pipeline.site.top_datasets_used, **_site_kwargs(cfg, args, data_dir))
    print(f"输出目录：{r.site_dir}")
    print(f"论文 {r.n_papers} 篇，周报 {r.n_weekly} 份，资源 {r.n_resources} 条")
    print(f"新闻：核心 {r.n_news} 条、相关 {r.n_news_related} 条")
    print("预览：python -m aivc serve")
    return 0


def _run_serve(cfg, args, data_dir, reports_dir) -> int:
    port = args.port if args.port is not None else cfg.pipeline.site.port
    if not (0 <= port <= 65535):
        raise ValueError(f"端口无效：{port}")
    if not (Path(args.site_dir) / "index.html").exists():
        print("未找到已生成的网站，先自动建站……")
        _run_build_site(cfg, args, data_dir, reports_dir)
    try:
        site.serve(args.site_dir, port)
    except KeyboardInterrupt:
        return 0
    except OSError as e:
        print(f"错误：无法在端口 {port} 启动服务：{e}", file=sys.stderr)
        return 2
    return 0


def _run_check_public(cfg, args, data_dir, reports_dir) -> int:
    root = Path(args.config_dir).resolve().parent
    issues, notes = publiccheck.run_checks(
        root=root, data_dir=data_dir, reports_dir=reports_dir, site_dir=args.site_dir,
        local_dir=Path(args.local_dir), allowed_tags=cfg.pipeline.public.allowed_tags)
    for n in notes:
        print(n)
    if not issues:
        print("check-public 通过：未发现问题")
        return 0
    for i in issues:
        print(i)
    print(f"check-public 未通过：共 {len(issues)} 个问题", file=sys.stderr)
    return 1


def _run_refresh_openreview(cfg, args, data_dir) -> int:
    from . import openreview_refresh
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit 必须为正整数")
    r = openreview_refresh.run_refresh(cfg, data_dir=data_dir, local_dir=Path(args.local_dir),
                                       dry_run=args.dry_run, limit=args.limit)
    return 1 if r.failed else 0


def make_github_client(cfg) -> GitHubClient:
    """GITHUB_TOKEN 来自环境变量（可选）；测试里替换本函数以注入 mock transport。"""
    return GitHubClient(client=build_github_client(cfg.sources.http.user_agent, cfg.sources.http.timeout),
                        base_url=cfg.pipeline.resources.github_api_base)


def print_refresh_report(rep, out=None) -> None:
    out = out or sys.stdout
    w = lambda m: print(m, file=out)  # noqa: E731
    w(f"GitHub 指标刷新：目标 {rep.total_targets} 个仓库，请求 {rep.requests} 次")
    w(f"成功 {rep.ok} / 未找到 {rep.not_found} / 出错 {rep.errors} / 近期已抓取跳过 {rep.skipped_recent}"
      f" / 因限流或上限未处理 {rep.deferred}")
    for n in rep.not_found_names:
        w(f"  未找到（404）：{n}")
    for a, b in rep.renamed:
        w(f"  已改名：{a} -> {b}")
    for e in rep.error_details:
        w(f"  出错：{e}")
    if rep.stop_reason:
        w(f"提前停止：{rep.stop_reason}")
    if rep.retry_at is not None:
        w(f"下次可重试时间：{resources_mod._iso(rep.retry_at)}")
    elif rep.deferred:
        w("提示：再次运行 `aivc resources refresh` 继续处理剩余仓库")


def _refresh_resources(cfg, data_dir, *, force=False, limit=None):
    client = make_github_client(cfg)
    return resources_mod.refresh_github(cfg.pipeline.resources, data_dir=data_dir, client=client,
                                        force=force, limit=limit)


def _run_resources_discover(cfg, args, data_dir, store) -> int:
    papers = PaperStore(Path(data_dir)).load_all()
    news = NewsStore(Path(data_dir)).load_all()
    res = resources_mod.discover(papers, news, store.load_curated(), store.load_candidates(),
                                 cfg.pipeline.resources, date.today())
    kinds: dict[str, int] = {}
    for r in res.new:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    tag = "（dry-run，未写盘）" if args.dry_run else ""
    print(f"扫描论文 {len(papers)} 篇、新闻 {len(news)} 条；新增候选 {len(res.new)} 个"
          + (f"（{', '.join(f'{k}={n}' for k, n in kinds.items())}）" if kinds else "")
          + f"；合并来源 {len(res.merged)} 个已有候选{tag}")
    for r in res.new[:20]:
        print(f"  {r.id}  [{r.kind}]  {r.url or '(无 url)'}  来源 {len(r.discovered_from)} 个")
    if len(res.new) > 20:
        print(f"  ……另有 {len(res.new) - 20} 个")
    if not args.dry_run and (res.new or res.merged):
        store.save_candidates(res.candidates)
    return 0


def _run_resources(cfg, args, data_dir) -> int:
    store = resources_mod.ResourceStore(data_dir)
    cmd = args.resources_command
    if cmd == "check":
        problems = store.validate(cfg)
        for p in problems:
            print(p)
        if problems:
            print(f"resources check 未通过：共 {len(problems)} 个问题", file=sys.stderr)
            return 1
        print(f"resources check 通过：精选 {len(store.load_curated())} 条，候选 {len(store.load_candidates())} 条")
        return 0
    if cmd == "list":
        items = store.load_curated() + store.load_candidates()
        metrics = resources_mod.MetricsStore(data_dir).load()
        for line in resources_mod.list_lines(items, metrics, date.today(),
                                             cfg.pipeline.resources.maintained_within_days,
                                             status=args.status, kind=args.kind):
            print(line)
        return 0
    if cmd == "discover":
        return _run_resources_discover(cfg, args, data_dir, store)
    if cmd == "accept":
        acc, warns = resources_mod.accept_candidate(store, args.id, kind=args.kind, url=args.url,
                                                    name=args.name, description=args.description)
        print(f"已 accept：{acc.id}（{acc.kind}）-> {resources_mod.file_for_kind(acc.kind)}  {acc.url}")
        for w in warns:
            print(w)
        return 0
    if cmd == "archive":
        fn, msg = resources_mod.archive_resource(store, args.id)
        print(f"{args.id}：{msg}")
        return 0
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit 必须为正整数")
    rep = _refresh_resources(cfg, data_dir, force=args.force, limit=args.limit)
    print_refresh_report(rep)
    return 1 if rep.errors else 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # 读取当前目录 .env，不覆盖已有环境变量
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    _setup_logging(args.verbose)
    try:
        cfg = load_config(args.config_dir)
        data_dir, reports_dir = args.data_dir, args.reports_dir
        local_dir = Path(args.local_dir)
        report_tp = None
        if args.local:
            reports_dir = local_dir / "reports"
            args.site_dir = local_dir / "site"
            report_tp = report_topics(_local_topics(cfg, args))
            print(f"本机私有视图（--local）：报告输出到 {reports_dir}，网站输出到 {args.site_dir}")
        use_llm = not getattr(args, "no_llm", False)
        news_s = None
        if args.command == "update":
            if args.days is not None and args.days < 1:
                raise PipelineError("--days 必须为正整数")
            pinned: list = []
            if (cfg.pipeline.news.run_with_update and cfg.sources.news.enabled
                    and not args.no_news):
                try:
                    news_s = news_pipeline.run_news(cfg, data_dir=data_dir, days=args.days, use_llm=use_llm)
                    today = date.today()
                    pinned = sorted((x for x in NewsStore(data_dir).load_all()
                                     if x.major and x.first_seen == today),
                                    key=lambda x: (-(x.relevance or 0), x.id))
                except Exception as e:  # noqa: BLE001  新闻失败不影响论文更新
                    print(f"警告：新闻步骤失败：{e}", file=sys.stderr)
            s = pipeline.run_update(cfg, data_dir=data_dir, reports_dir=reports_dir,
                                    days=args.days, use_llm=use_llm, topics=report_tp,
                                    pinned_news=pinned or None)
            if args.refresh_resources or cfg.pipeline.resources.run_with_update:
                try:
                    print_refresh_report(_refresh_resources(cfg, data_dir))
                except Exception as e:  # noqa: BLE001  资源刷新失败不影响论文更新
                    print(f"警告：资源指标刷新失败：{e}", file=sys.stderr)
        elif args.command == "news":
            return _run_news(cfg, args, data_dir, reports_dir)
        elif args.command == "news-rescore":
            return _run_news_rescore(cfg, args, data_dir, reports_dir)
        elif args.command == "backfill":
            s = pipeline.run_backfill(cfg, data_dir=data_dir, reports_dir=reports_dir,
                                      days=args.days, use_llm=use_llm, topics=report_tp)
        elif args.command == "seed":
            src = args.source_dir or os.environ.get("AIVC_SEED_DIR") or cfg.sources.seed.source_dir
            if not src:
                raise PipelineError("未指定种子目录：请用 --source-dir、环境变量 AIVC_SEED_DIR "
                                    "或 config/sources.yaml 的 seed.source_dir")
            md = args.iclr_md or os.environ.get("AIVC_SEED_ICLR_MD") or cfg.sources.seed.iclr_md
            # 种子是私有数据：一律写到 <local-dir>/data，公开库只读
            s = pipeline.run_seed(cfg, data_dir=local_dir / "data", source_dir=Path(src),
                                  iclr_md=Path(md) if md else None, use_llm=use_llm,
                                  public_data_dir=data_dir)
        elif args.command == "reindex":
            s = pipeline.run_reindex(cfg, data_dir=data_dir, prune=args.prune)
        elif args.command == "score":
            s = pipeline.run_score(cfg, data_dir=data_dir, limit=args.limit)
        elif args.command == "weekly":
            return _run_weekly(cfg, args, data_dir, reports_dir)
        elif args.command == "resources":
            return _run_resources(cfg, args, data_dir)
        elif args.command == "refresh-openreview":
            return _run_refresh_openreview(cfg, args, data_dir)
        elif args.command == "check-public":
            return _run_check_public(cfg, args, data_dir, reports_dir)
        elif args.command == "build-site":
            return _run_build_site(cfg, args, data_dir, reports_dir)
        else:
            return _run_serve(cfg, args, data_dir, reports_dir)
    except (PipelineError, FileNotFoundError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    print_summary(s)
    if args.command == "update" and news_s is not None:
        print(_news_line(news_s))
    if s.all_sources_failed:
        print("错误：所有启用的源均抓取失败", file=sys.stderr)
        return 1
    if args.command == "update":
        _auto_build_site(cfg, args, data_dir, reports_dir)
    return 0
