import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const N = require(path.join(here, "../../src/aivc/web/assets/news-data.js"));

const ITEMS = [
  { id: "a", title: "Alpha raises Series B", date: "2026-11-10", week: "2026-W46", event_type: "funding", amount_usd: 120e6,
    companies: ["Alpha Bio"], company_ids: ["alpha"], investors: ["Fund One"], source: "Wire", sources: [{ name: "Wire", url: "https://x.test/a" }],
    summary_zh: "某公司完成融资", significance_zh: "规模较大", relevance: 8, scored: true, major: true },
  { id: "b", title: "Beta partners with Gamma", date: "2026-11-10", week: "2026-W46", event_type: "partnership", amount_usd: null,
    companies: ["Beta"], company_ids: ["beta"], investors: [], source: "Blog", sources: [], summary_zh: "合作", significance_zh: "", relevance: 5, scored: true, major: false },
  { id: "c", title: "Delta seed round", date: "2026-11-04", week: "2026-W45", event_type: "funding", amount_usd: 5e6,
    companies: ["Delta"], company_ids: [], investors: ["Angel Co"], source: "Wire", sources: [], summary_zh: "种子轮", significance_zh: "", relevance: 4, scored: true, major: false },
  { id: "d", title: "Epsilon mid round", date: "2026-11-05", week: "2026-W45", event_type: "funding", amount_usd: 30e6,
    companies: ["Epsilon"], company_ids: [], investors: [], source: "Wire", sources: [], summary_zh: "", significance_zh: "", relevance: 7, scored: true, major: false },
  { id: "e", title: "Zeta unscored", date: "2026-11-06", week: "2026-W45", event_type: "other", amount_usd: null,
    companies: [], company_ids: [], investors: [], source: "Blog", sources: [], summary_zh: "", significance_zh: "", relevance: 0, scored: false, major: false }
];

test("filterNews 默认隐藏未处理，includeUnscored 可打开", () => {
  assert.equal(N.filterNews(ITEMS, {}).length, 4);
  assert.equal(N.filterNews(ITEMS, { includeUnscored: true }).length, 5);
  assert.equal(N.filterNews(null, {}).length, 0);
});

test("filterNews 按类型、公司 id 与名称、重大", () => {
  assert.deepEqual(N.filterNews(ITEMS, { type: "funding" }).map((x) => x.id), ["a", "c", "d"]);
  assert.deepEqual(N.filterNews(ITEMS, { company: "alpha" }).map((x) => x.id), ["a"]);
  assert.deepEqual(N.filterNews(ITEMS, { company: "delta" }).map((x) => x.id), ["c"]);
  assert.deepEqual(N.filterNews(ITEMS, { major: true }).map((x) => x.id), ["a"]);
});

test("filterNews 金额档位边界", () => {
  assert.deepEqual(N.filterNews(ITEMS, { amount: "ge100m" }).map((x) => x.id), ["a"]);
  assert.deepEqual(N.filterNews(ITEMS, { amount: "10m_100m" }).map((x) => x.id), ["d"]);
  assert.deepEqual(N.filterNews(ITEMS, { amount: "lt10m" }).map((x) => x.id), ["c"]);
  assert.deepEqual(N.filterNews(ITEMS, { amount: "undisclosed" }).map((x) => x.id), ["b"]);
  const edge = [{ id: "x", amount_usd: 1e8, scored: true }, { id: "y", amount_usd: 1e7, scored: true }];
  assert.deepEqual(N.filterNews(edge, { amount: "ge100m" }).map((x) => x.id), ["x"]);
  assert.deepEqual(N.filterNews(edge, { amount: "10m_100m" }).map((x) => x.id), ["y"]);
});

test("filterNews 关键词覆盖标题/摘要/要点/公司/投资方/来源，不区分大小写，多词为与", () => {
  assert.deepEqual(N.filterNews(ITEMS, { q: "ALPHA" }).map((x) => x.id), ["a"]);
  assert.deepEqual(N.filterNews(ITEMS, { q: "种子" }).map((x) => x.id), ["c"]);
  assert.deepEqual(N.filterNews(ITEMS, { q: "规模" }).map((x) => x.id), ["a"]);
  assert.deepEqual(N.filterNews(ITEMS, { q: "angel co" }).map((x) => x.id), ["c"]);
  assert.deepEqual(N.filterNews(ITEMS, { q: "blog" }).map((x) => x.id), ["b"]);
  assert.deepEqual(N.filterNews(ITEMS, { q: "wire funding" }).length, 0);
  assert.deepEqual(N.filterNews(ITEMS, { q: "wire series" }).map((x) => x.id), ["a"]);
});

test("filterNews 组合条件", () => {
  assert.deepEqual(N.filterNews(ITEMS, { type: "funding", amount: "lt10m", q: "delta" }).map((x) => x.id), ["c"]);
  assert.equal(N.filterNews(ITEMS, { type: "partnership", major: true }).length, 0);
});

test("weekRange 与 ISO 周对齐", () => {
  assert.deepEqual(N.weekRange("2026-W46"), { start: "2026-11-09", end: "2026-11-15" });
  assert.deepEqual(N.weekRange("2026-W01"), { start: "2025-12-29", end: "2026-01-04" });
  assert.equal(N.weekRange("bad"), null);
  assert.equal(N.weekRange("2026-W00"), null);
});

test("groupByWeek 周倒序、组内相关度降序再日期降序", () => {
  const g = N.groupByWeek(ITEMS);
  assert.deepEqual(g.map((x) => x.id), ["2026-W46", "2026-W45"]);
  assert.equal(g[0].start, "2026-11-09");
  assert.equal(g[0].end, "2026-11-15");
  assert.deepEqual(g[0].items.map((x) => x.id), ["a", "b"]);
  assert.deepEqual(g[1].items.map((x) => x.id), ["d", "c", "e"]);
  assert.deepEqual(N.groupByWeek([]), []);
});

const mkI = (id, rel, date, co) => ({ id, relevance: rel, date, week: "2026-W46", companies: co ? [co] : [], company_ids: co ? [co] : [] });

test("interleaveByCompany 同键区段按公司轮转，不同键区段顺序不变", () => {
  const xs = [mkI("a1", 7, "d3", "A"), mkI("a2", 7, "d2", "A"), mkI("a3", 7, "d1", "A"),
    mkI("b1", 7, "d3", "B"), mkI("n1", 7, "d2"), mkI("n2", 7, "d1"), mkI("c1", 5, "d3", "C"), mkI("c2", 5, "d2", "C")];
  const out = N.interleaveByCompany(xs, (x) => x.relevance);
  assert.deepEqual(out.map((x) => x.id), ["a1", "b1", "n1", "n2", "a2", "a3", "c1", "c2"]);
  assert.deepEqual(N.interleaveByCompany([], (x) => x), []);
  assert.deepEqual(N.interleaveByCompany(xs, () => "same").map((x) => x.id), ["a1", "b1", "n1", "n2", "c1", "a2", "c2", "a3"]);
});

test("interleaveByCompany 回退到 companies[0]，公司自身顺序稳定", () => {
  const xs = [{ id: "p1", companies: ["P"] }, { id: "p2", companies: ["P"] }, { id: "q1", companies: ["Q"], company_ids: [] }];
  assert.deepEqual(N.interleaveByCompany(xs, () => 0).map((x) => x.id), ["p1", "q1", "p2"]);
});

test("groupByWeek 同分时不同公司交替，相关度仍优先", () => {
  const xs = [mkI("a1", 6, "2026-11-10", "A"), mkI("a2", 6, "2026-11-09", "A"), mkI("b1", 6, "2026-11-08", "B"),
    mkI("c1", 8, "2026-11-01", "C"), mkI("a3", 4, "2026-11-12", "A")];
  const g = N.groupByWeek(xs);
  assert.deepEqual(g[0].items.map((x) => x.id), ["c1", "a1", "b1", "a2", "a3"]);
});

test("fmtUsd 档位与未披露", () => {
  assert.equal(N.fmtUsd(1.2e9), "$1.2B");
  assert.equal(N.fmtUsd(2e9), "$2B");
  assert.equal(N.fmtUsd(120e6), "$120M");
  assert.equal(N.fmtUsd(1.5e6), "$1.5M");
  assert.equal(N.fmtUsd(850e3), "$850K");
  assert.equal(N.fmtUsd(null), "未披露");
  assert.equal(N.fmtUsd(undefined), "未披露");
  assert.equal(N.fmtUsd(NaN), "未披露");
});

test("quarterBars 相对高度", () => {
  const b = N.quarterBars([
    { quarter: "2026Q3", total_usd: 50e6, n_events: 2, n_undisclosed: 0, top: [] },
    { quarter: "2026Q4", total_usd: 200e6, n_events: 1, n_undisclosed: 1, top: [] },
    { quarter: "2027Q1", total_usd: 0, n_events: 0, n_undisclosed: 2, top: [] }
  ]);
  assert.equal(b[1].h, 1);
  assert.equal(b[0].h, 0.25);
  assert.equal(b[2].h, 0);
  assert.equal(b[0].quarter, "2026Q3");
  assert.deepEqual(N.quarterBars(null), []);
  assert.equal(N.quarterBars([{ quarter: "x", total_usd: 1 }])[0].h, 1);
});

test("filterNews tier：默认核心，related 只要相关，all 全要；缺 tier 视为 core", () => {
  const T = [
    { id: "c1", relevance: 7, scored: true, tier: "core" },
    { id: "c2", relevance: 6, scored: true },
    { id: "r1", relevance: 5, scored: true, tier: "related" },
    { id: "u1", relevance: 0, scored: false, tier: null }
  ];
  assert.deepEqual(N.filterNews(T, {}).map((x) => x.id), ["c1", "c2"]);
  assert.deepEqual(N.filterNews(T, { tier: "core" }).map((x) => x.id), ["c1", "c2"]);
  assert.deepEqual(N.filterNews(T, { tier: "related" }).map((x) => x.id), ["r1"]);
  assert.deepEqual(N.filterNews(T, { tier: "all" }).map((x) => x.id), ["c1", "c2", "r1"]);
  assert.deepEqual(N.filterNews(T, { tier: "all", includeUnscored: true }).map((x) => x.id), ["c1", "c2", "r1", "u1"]);
  assert.deepEqual(N.filterNews(ITEMS, { tier: "related" }), []);
});

test("isRelated", () => {
  assert.equal(N.isRelated({ tier: "related" }), true);
  assert.equal(N.isRelated({ tier: "core" }), false);
  assert.equal(N.isRelated({}), false);
  assert.equal(N.isRelated(null), false);
});
