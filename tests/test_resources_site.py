from datetime import date

import pytest

from aivc.config import load_config
from aivc.models import Resource
from aivc.resources_site import KIND_LABELS, RESOURCE_GITHUB_KEYS, RESOURCE_ITEM_KEYS, export_resources

TODAY = date(2026, 11, 15)
GEN = "2026-11-15T00:00:00"


@pytest.fixture
def cfg(config_dir):
    return load_config(config_dir)


def R(rid, kind="repo", **kw):
    base = dict(id=rid, name=kw.pop("name", rid), kind=kind, url=f"https://example.org/{rid}",
                description_zh="一句话说明", added=date(2026, 11, 1))
    base.update(kw)
    return Resource(**base)


def _exp(cfg, items, metrics=None, papers=(), n=0):
    return export_resources(items, metrics, papers, cfg, today=TODAY, generated_at=GEN, n_candidates=n)


def test_only_accepted_and_no_private_fields(cfg):
    items = [R("a"), R("b", status="candidate", note="n", discovered_from=["p1"]),
             R("c", status="archived", url="")]
    out = _exp(cfg, items, n=1)
    assert [i["id"] for i in out["items"]] == ["a"]
    assert out["count"] == 1 and out["candidates_count"] == 1
    assert set(out["items"][0]) == set(RESOURCE_ITEM_KEYS)
    assert "note" not in out["items"][0] and "discovered_from" not in out["items"][0]
    assert set(out) == {"generated_at", "metrics_fetched_at", "count", "candidates_count",
                        "maintained_within_days", "kinds", "sections", "tasks", "paradigms", "items"}


def test_kinds_order_and_counts(cfg):
    out = _exp(cfg, [R("x", "course"), R("y", "repo"), R("z", "repo")])
    assert [k["id"] for k in out["kinds"]] == [k for k, _ in KIND_LABELS]
    assert [k["id"] for k in out["kinds"]][:4] == ["lab", "repo", "tool", "data_portal"]
    c = {k["id"]: k["count"] for k in out["kinds"]}
    assert c["repo"] == 2 and c["course"] == 1 and c["podcast"] == 0
    assert out["kinds"][1]["label"] == "模型与方法仓库"
    assert [i["kind"] for i in out["items"]] == ["repo", "repo", "course"]


def _m(stars, pushed="2026-10-01", archived=False, hist=None, status="ok"):
    return {"full_name": "O/Repo", "html_url": "https://github.com/O/Repo", "stars": stars, "pushed_at": pushed,
            "archived": archived, "license": "MIT", "language": "Python", "fetched_at": "2026-11-14T00:00:00+00:00",
            "status": status, "history": hist or []}


def test_github_merge_maintained_delta_and_sort(cfg):
    items = [R("nogh", name="Zeta"), R("low", github="o/low", name="Low"), R("hi", github="o/hi", name="Hi"),
             R("old", github="o/old", name="Old"), R("arch", github="o/arch", name="Arch"),
             R("alpha", name="Alpha")]
    metrics = {"fetched_at": "2026-11-14T00:00:00+00:00", "repos": {
        "o/low": _m(10, hist=[{"date": "2026-10-01", "stars": 4}, {"date": "2026-11-14", "stars": 10}]),
        "o/hi": _m(500, hist=[{"date": "2026-11-14", "stars": 500}]),
        "o/old": _m(50, pushed="2024-01-01"),
        "o/arch": _m(5, archived=True),
    }}
    out = _exp(cfg, items, metrics)
    ids = [i["id"] for i in out["items"]]
    assert ids == ["hi", "old", "low", "arch", "alpha", "nogh"]
    by = {i["id"]: i for i in out["items"]}
    assert out["metrics_fetched_at"] == "2026-11-14T00:00:00+00:00"
    g = by["low"]["github"]
    assert set(g) == set(RESOURCE_GITHUB_KEYS)
    assert g["stars_delta"] == 6 and g["maintained"] is True and g["license"] == "MIT"
    assert by["hi"]["github"]["stars_delta"] is None
    assert by["old"]["github"]["maintained"] is False
    assert by["arch"]["github"]["maintained"] is False and by["arch"]["github"]["archived"] is True
    assert by["alpha"]["github"] is None


def test_metrics_error_status_maintained_null(cfg):
    out = _exp(cfg, [R("e", github="o/e")], {"repos": {"o/e": _m(3, status="not_found")}})
    g = out["items"][0]["github"]
    assert g["maintained"] is None and g["status"] == "not_found"


def test_paper_ids_filtered(cfg):
    out = _exp(cfg, [R("a", paper_ids=["p1", "gone", "p2"], dataset_names=["Norman"])], papers=["p1", "p2"])
    assert out["items"][0]["paper_ids"] == ["p1", "p2"]
    assert out["items"][0]["dataset_names"] == ["Norman"]


def test_tasks_paradigms_only_used(cfg):
    out = _exp(cfg, [R("a", related_tasks=["spatial"], related_paradigms=["llm_agent"])])
    assert [t["id"] for t in out["tasks"]] == ["spatial"]
    assert out["tasks"][0]["label"] == "空间组学"
    assert [p["id"] for p in out["paradigms"]] == ["llm_agent"]


def test_empty(cfg):
    out = _exp(cfg, [], None)
    assert out["count"] == 0 and out["items"] == [] and out["metrics_fetched_at"] is None
    assert len(out["kinds"]) == len(KIND_LABELS) and all(k["count"] == 0 for k in out["kinds"])
    assert out["tasks"] == [] and out["paradigms"] == []
    assert out["maintained_within_days"] == cfg.pipeline.resources.maintained_within_days


def test_sections_cover_every_kind_once(cfg):
    from aivc.resources_site import SECTIONS
    kinds = [k for _, _, ks in SECTIONS for k in ks]
    assert len(kinds) == len(set(kinds))
    assert set(kinds) == {k for k, _ in KIND_LABELS}
    out = _exp(cfg, [R("a", "lab"), R("b", "repo"), R("c", "tool"), R("d", "awesome_list"), R("e", "review")])
    sec = {s["id"]: s for s in out["sections"]}
    assert [s["id"] for s in out["sections"]] == ["labs", "repos", "data", "reading", "lists"]
    assert sec["labs"]["count"] == 1 and sec["repos"]["count"] == 2 and sec["lists"]["count"] == 1
    assert sec["data"]["count"] == 0 and sec["reading"]["count"] == 1
