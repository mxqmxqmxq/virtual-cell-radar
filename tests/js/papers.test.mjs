import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const P = require(path.join(here, "../../src/aivc/web/assets/papers-data.js"));

const base = {
  abstract: "", authors: [], source: "arxiv", sources: [], venue: "", published_venue: "", doi: "", published_doi: "",
  date: "2026-10-01", scored: false, final_score: null, keyword_score: 0, must_read: false, relevance: null,
  novelty_score: null, rigor_score: null, impact_score: null, citation_count: 0, hf_upvotes: 0, github_stars: 0,
  code_url: "", weights_url: "", matched_orgs: [], tasks: [], paradigms: [], releases_new_dataset: false,
  keywords: [], matched_keywords: [], datasets_used: [], new_dataset_names: [], novelty_zh: "",
  why_it_matters_zh: "", summary_zh: "", circle: "core"
};
const mk = (o) => ({ ...base, ...o });
const mkAll = () => [
  // direct journal publication
  mk({ id: "a1", title: "Direct Journal Paper", source: "openalex", sources: ["openalex"], venue: "Nucleic Acids Research",
    published_venue: "Nucleic Acids Research", doi: "10.1/a1", published_doi: "10.1/a1", date: "2026-10-05",
    scored: true, final_score: 6.5, keyword_score: 3, relevance: 6, novelty_score: 6, rigor_score: 7, impact_score: 5,
    matched_orgs: ["Harvard"], tasks: ["pert"], circle: "core" }),
  // preprint promoted to journal
  mk({ id: "a2", title: "Promoted Preprint", source: "biorxiv", sources: ["biorxiv", "openalex"], published_venue: "Cell Systems",
    doi: "10.1101/a2", published_doi: "10.1016/a2", date: "2026-10-04", scored: true, final_score: 8.1, keyword_score: 5,
    relevance: 8, novelty_score: 9, rigor_score: 8, impact_score: 7, must_read: true, matched_orgs: ["Harvard", "EMBL"],
    code_url: "https://github.com/o/a2", github_stars: 310, citation_count: 12, paradigms: ["fm"], releases_new_dataset: true,
    new_dataset_names: ["AtlasX"], why_it_matters_zh: "为何重要的话", novelty_zh: "创新点" }),
  // pure preprint, v4 partial (impact null)
  mk({ id: "a3", title: "Pure Preprint Partial", source: "arxiv", sources: ["arxiv", "hf_papers"], doi: "10.48/a3",
    date: "2026-10-04", scored: true, final_score: 7.0, keyword_score: 4, relevance: 7, novelty_score: 8, rigor_score: null,
    impact_score: 6, hf_upvotes: 40, weights_url: "https://huggingface.co/o/a3", tasks: ["state"], circle: "adjacent" }),
  // v3 scored (no v4 dims)
  mk({ id: "a4", title: "Legacy V3", source: "arxiv", date: "2026-09-30", scored: true, final_score: 5.0, keyword_score: 2,
    relevance: 5, citation_count: 100 }),
  // unscored, high keyword
  mk({ id: "a5", title: "Unscored Hot", source: "medrxiv", date: "2026-10-06", keyword_score: 9, hf_upvotes: 5 }),
  // unscored, low keyword
  mk({ id: "a6", title: "Unscored Cold", source: "chemrxiv", date: "2026-10-04", keyword_score: 1 }),
  // v4 all four, same final as a1
  mk({ id: "a7", title: "V4 Tie", source: "arxiv", date: "2026-10-03", scored: true, final_score: 6.5, keyword_score: 3,
    relevance: 6, novelty_score: 6, rigor_score: 6, impact_score: 6, citation_count: 12, matched_orgs: ["Harvard"] }),
  // v4 with null final
  mk({ id: "a8", title: "V4 No Final", source: "arxiv", date: "2026-10-02", scored: true, final_score: null, keyword_score: 0,
    relevance: 4, novelty_score: 3, rigor_score: 3, impact_score: 3 })
];
const ids = (l) => l.map((x) => x.id);
const F = (f, o) => ids(P.filterAndSortPapers(mkAll(), f, o));

test("sourceLabel：已知 / 未知 / 空值", () => {
  assert.equal(P.sourceLabel("biorxiv"), "bioRxiv");
  assert.equal(P.sourceLabel("hf_papers"), "HF Papers");
  assert.equal(P.sourceLabel("seed_pdf"), "本地 PDF");
  assert.equal(P.sourceLabel("zzz"), "zzz");
  assert.equal(P.sourceLabel(null), "");
  assert.equal(P.sourceLabel(undefined), "");
  assert.equal(Object.keys(P.SOURCE_LABELS).length, 12);
});

test("allSourcesOf：去重保序，主来源在前", () => {
  assert.deepEqual(P.allSourcesOf({ source: "biorxiv", sources: ["openalex", "biorxiv", "arxiv", "openalex"] }), ["biorxiv", "openalex", "arxiv"]);
  assert.deepEqual(P.allSourcesOf({ source: "arxiv" }), ["arxiv"]);
  assert.deepEqual(P.allSourcesOf({ sources: ["a", "b"] }), ["a", "b"]);
  assert.deepEqual(P.allSourcesOf({}), []);
  assert.deepEqual(P.allSourcesOf(null), []);
});

test("pubInfo：直接发表", () => {
  const [a1] = mkAll();
  assert.deepEqual(P.pubInfo(a1), { published: true, venue: "Nucleic Acids Research", doi: "10.1/a1", isPromoted: false });
});

test("pubInfo：预印本晋升", () => {
  const a2 = mkAll()[1];
  assert.deepEqual(P.pubInfo(a2), { published: true, venue: "Cell Systems", doi: "10.1016/a2", isPromoted: true });
});

test("pubInfo：纯预印本 / 空值", () => {
  const a3 = mkAll()[2];
  assert.deepEqual(P.pubInfo(a3), { published: false, venue: "", doi: "10.48/a3", isPromoted: false });
  assert.equal(P.pubInfo(null).published, false);
  assert.equal(P.pubInfo({ published_venue: "   " }).published, false);
});

test("rubricDims：v4 四维齐全，顺序与简称", () => {
  const d = P.rubricDims(mkAll()[1]);
  assert.deepEqual(d.map((x) => x.key), ["relevance", "novelty", "rigor", "impact"]);
  assert.deepEqual(d.map((x) => x.short), ["相", "新", "严", "影"]);
  assert.deepEqual(d.map((x) => x.label), ["相关度", "新颖度", "严谨度", "影响力"]);
  assert.deepEqual(d.map((x) => x.value), [8, 9, 8, 7]);
});

test("rubricDims：部分维度为 null 只保留有值项", () => {
  const d = P.rubricDims(mkAll()[2]);
  assert.deepEqual(d.map((x) => x.key), ["relevance", "novelty", "impact"]);
});

test("rubricDims：v3 / 未打分 / 空 -> null", () => {
  const all = mkAll();
  assert.equal(P.rubricDims(all[3]), null);
  assert.equal(P.rubricDims(all[4]), null);
  assert.equal(P.rubricDims(mk({ scored: false, novelty_score: 8 })), null);
  assert.equal(P.rubricDims(null), null);
});

test("collectSources：合并 by_source 与各篇 sources，升序，友好标签", () => {
  const meta = { stats: { by_source: { openalex: 4, zzz_new: 1 } } };
  const r = P.collectSources(mkAll(), meta);
  assert.deepEqual(r.map((x) => x[0]), ["arxiv", "biorxiv", "chemrxiv", "hf_papers", "medrxiv", "openalex", "zzz_new"]);
  assert.deepEqual(r.find((x) => x[0] === "hf_papers"), ["hf_papers", "HF Papers"]);
  assert.deepEqual(r.find((x) => x[0] === "zzz_new"), ["zzz_new", "zzz_new"]);
  assert.deepEqual(P.collectSources([], null), []);
  assert.deepEqual(P.collectSources(null, {}), []);
});

test("collectOrgs：频次降序、同频字母序、容错", () => {
  assert.deepEqual(P.collectOrgs(mkAll()), [{ name: "Harvard", count: 3 }, { name: "EMBL", count: 1 }]);
  const tie = [mk({ id: "x", matched_orgs: ["B", "A"] }), mk({ id: "y", matched_orgs: ["C", "A", " "] })];
  assert.deepEqual(P.collectOrgs(tie), [{ name: "A", count: 2 }, { name: "B", count: 1 }, { name: "C", count: 1 }]);
  assert.deepEqual(P.collectOrgs([]), []);
  assert.deepEqual(P.collectOrgs([{ id: "z" }, null]), []);
  assert.deepEqual(P.collectOrgs(null), []);
});

test("paperHaystack：小写并含期刊 / 机构 / 为何重要", () => {
  const h = P.paperHaystack(mkAll()[1]);
  assert.equal(h, h.toLowerCase());
  for (const s of ["promoted preprint", "cell systems", "harvard", "embl", "为何重要的话", "atlasx", "创新点"]) {
    assert.ok(h.includes(s), s);
  }
  assert.equal(P.paperHaystack(null), "");
  assert.equal(P.paperHaystack({}).trim(), "");
  assert.equal(P.paperHaystack(Object.freeze({ title: "Frozen" })).trim(), "frozen");
});

test("filterAndSortPapers：无条件返回全部，不改入参顺序", () => {
  const all = mkAll();
  const before = ids(all);
  const r = P.filterAndSortPapers(all, {});
  assert.equal(r.length, 8);
  assert.deepEqual(ids(all), before);
  assert.notEqual(r, all);
  assert.equal(P.filterAndSortPapers(all).length, 8);
});

test("filterAndSortPapers：papers 为 null / 含 null 元素容错", () => {
  assert.deepEqual(P.filterAndSortPapers(null, {}), []);
  assert.deepEqual(P.filterAndSortPapers(undefined), []);
  assert.deepEqual(ids(P.filterAndSortPapers([null, mk({ id: "k" }), 3], {})), ["k"]);
});

test("q：搜索期刊名、机构名，多词 AND", () => {
  assert.deepEqual(F({ q: "cell systems" }), ["a2"]);
  assert.deepEqual(F({ q: "NUCLEIC" }), ["a1"]);
  assert.deepEqual(F({ q: "embl" }), ["a2"]);
  assert.deepEqual(F({ q: "harvard" }), ["a1", "a2", "a7"]);
  assert.deepEqual(F({ q: "harvard embl" }), ["a2"]);
  assert.deepEqual(F({ q: "harvard nonexistent" }), []);
  assert.deepEqual(F({ q: "   " }).length, 8);
});

test("source：多源论文的次来源也命中", () => {
  assert.deepEqual(F({ source: "openalex" }), ["a1", "a2"]);
  assert.deepEqual(F({ source: "hf_papers" }), ["a3"]);
  assert.deepEqual(F({ source: "arxiv" }), ["a3", "a7", "a8", "a4"]);
  assert.deepEqual(F({ source: "none" }), []);
});

test("pub / code / weights / org / must / newds", () => {
  assert.deepEqual(F({ pub: "1" }), ["a1", "a2"]);
  assert.deepEqual(F({ code: "1" }), ["a2"]);
  assert.deepEqual(F({ weights: "1" }), ["a3"]);
  assert.deepEqual(F({ org: "Harvard" }), ["a1", "a2", "a7"]);
  assert.deepEqual(F({ org: "EMBL" }), ["a2"]);
  assert.deepEqual(F({ must: "1" }), ["a2"]);
  assert.deepEqual(F({ newds: "1" }), ["a2"]);
});

test("scored 1/0、task、paradigm、circle", () => {
  assert.deepEqual(F({ scored: "1" }).length, 6);
  assert.deepEqual(F({ scored: "0" }), ["a5", "a6"]);
  assert.deepEqual(F({ task: "pert" }), ["a1"]);
  assert.deepEqual(F({ task: "state" }), ["a3"]);
  assert.deepEqual(F({ paradigm: "fm" }), ["a2"]);
  assert.deepEqual(F({ circle: "adjacent" }), ["a3"]);
});

test("from / to 日期区间（含边界）", () => {
  assert.deepEqual(F({ from: "2026-10-04" }), ["a5", "a1", "a2", "a3", "a6"]);
  assert.deepEqual(F({ to: "2026-10-02" }), ["a8", "a4"]);
  assert.deepEqual(F({ from: "2026-10-03", to: "2026-10-04" }), ["a2", "a3", "a6", "a7"]);
});

test("topic：topicsOf 回调（any 与指定 id），缺 opts 不抛错", () => {
  const tm = { a1: [{ id: "t1" }], a3: [{ id: "t2" }, { id: "t1" }] };
  const opts = { topicsOf: (p) => tm[p.id] || [] };
  assert.deepEqual(F({ topic: "any" }, opts), ["a1", "a3"]);
  assert.deepEqual(F({ topic: "t1" }, opts), ["a1", "a3"]);
  assert.deepEqual(F({ topic: "t2" }, opts), ["a3"]);
  assert.deepEqual(F({ topic: "t9" }, opts), []);
  assert.doesNotThrow(() => P.filterAndSortPapers(mkAll(), { topic: "t1" }));
  assert.deepEqual(F({ topic: "t1" }), []);
  assert.doesNotThrow(() => P.filterAndSortPapers(mkAll(), { topic: "any" }, { topicsOf: () => null }));
});

test("组合筛选", () => {
  assert.deepEqual(F({ org: "Harvard", pub: "1" }), ["a1", "a2"]);
  assert.deepEqual(F({ org: "Harvard", pub: "1", code: "1" }), ["a2"]);
});

test("排序 date：新到旧，并列按 id", () => {
  assert.deepEqual(F({}), ["a5", "a1", "a2", "a3", "a6", "a7", "a8", "a4"]);
  assert.deepEqual(F({ sort: "date" }), F({}));
});

test("排序 final：已打分在前降序，未打分按 keyword_score", () => {
  // a2 8.1, a3 7.0, a1/a7 6.5 (并列 keyword 3 -> 日期 a1 新), a4 5.0, a8 final null 视为未打分档
  // a8 scored 但 final_score 为 null -> 与未打分同档，按 keyword 降序: a5(9) a6(1) a8(0)
  assert.deepEqual(F({ sort: "final" }), ["a2", "a3", "a1", "a7", "a4", "a5", "a6", "a8"]);
});

test("排序 novelty：有值在前降序，其次 final_score、日期", () => {
  // novelty: a2 9, a3 8, a1 6, a7 6, a8 3；a1/a7 并列按 final 同为 6.5，再日期 a1 新
  const r = F({ sort: "novelty" });
  assert.deepEqual(r.slice(0, 5), ["a2", "a3", "a1", "a7", "a8"]);
  // 无 novelty：a4 final 5.0 在前；a5/a6 无 final，按日期 a5(10-06) 先于 a6(10-04)
  assert.deepEqual(r.slice(5), ["a4", "a5", "a6"]);
});

test("排序 citations：正数在前降序，其次 final_score、日期", () => {
  // a4 100, a2 12 与 a7 12 并列 -> final 8.1 > 6.5
  const r = F({ sort: "citations" });
  assert.deepEqual(r.slice(0, 3), ["a4", "a2", "a7"]);
  // 其余无引用：按 final 降序 a3 7.0, a1 6.5, 然后无 final：a5(10-06) a6(10-04) a8(null final, 10-02)
  assert.deepEqual(r.slice(3), ["a3", "a1", "a5", "a6", "a8"]);
});

test("排序 hf：正数在前降序，其次 final_score、日期", () => {
  const r = F({ sort: "hf" });
  assert.deepEqual(r.slice(0, 2), ["a3", "a5"]);
  assert.deepEqual(r.slice(2, 5), ["a2", "a1", "a7"]);
});

test("排序 keyword：keyword_score 降序，其次日期", () => {
  // a5 9, a2 5, a3 4, a1 3 / a7 3 (日期 a1 新), a4 2, a6 1, a8 0
  assert.deepEqual(F({ sort: "keyword" }), ["a5", "a2", "a3", "a1", "a7", "a4", "a6", "a8"]);
});

test("缓存不污染：多次过滤结果稳定", () => {
  const all = mkAll();
  const r1 = ids(P.filterAndSortPapers(all, { q: "harvard" }));
  const r2 = ids(P.filterAndSortPapers(all, { q: "harvard" }));
  assert.deepEqual(r1, r2);
});

test("publication links accept publisher URLs and encode raw DOI punctuation", () => {
  assert.equal(P.doiUrl("10.1234/a#b"), "https://doi.org/10.1234/a%23b");
  assert.equal(P.doiUrl("https://example.org/paper"), "https://example.org/paper");
  assert.equal(P.pubInfo({source: "chemrxiv", published_venue: "Journal"}).isPromoted, true);
  for (const invalid of [ "javascript:void(0)", "10.1234/../a"])
    assert.equal(P.doiUrl(invalid), null);
});

test("filters require usable URLs and normalize institution names consistently", () => {
  const paper = mk({ matched_orgs: [" __proto__ ", "__proto__", null], source: "constructor",
    code_url: "javascript:void(0)", weights_url: "https://", _hay: "injected" });
  assert.deepEqual(P.collectOrgs([paper]), [{name: "__proto__", count: 1}]);
  assert.equal(P.collectSources([paper])[0][0], "constructor");
  assert.equal(P.filterAndSortPapers([paper], {org: "__proto__"}).length, 1);
  for (const filter of [{code: "1"}, {weights: "1"}, {q: "injected"}])
    assert.equal(P.filterAndSortPapers([paper], filter).length, 0);
});

test("nonfinite final scores and stale unscored novelty sort after zero", () => {
  const papers = [mk({id:"bad", scored:true, final_score:Infinity}), mk({id:"zero", scored:true, final_score:0, novelty_score:0}),
    mk({id:"stale", scored:false, novelty_score:10})];
  for (const sort of ["final", "novelty"]) assert.equal(P.filterAndSortPapers(papers, {sort})[0].id, "zero");
});

test("rubric prefers exported relevance, including zero, and falls back for legacy input", () => {
  const scored = mk({scored: true, relevance: 0, relevance_score: 8, impact_score: 6});
  assert.equal(P.rubricDims(scored)[0].value, 0);
  assert.equal(P.rubricDims({...scored, relevance: NaN})[0].value, 8);
});
