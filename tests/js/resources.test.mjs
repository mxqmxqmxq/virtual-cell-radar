import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const R = require(path.join(here, "../../src/aivc/web/assets/resources-data.js"));

const gh = (full, stars, maintained, extra = {}) => ({ full_name: full, stars, pushed_at: "2026-10-01T00:00:00Z", archived: false, license: "MIT", language: "Python", maintained, fetched_at: "x", stars_delta: null, status: "ok", ...extra });
const ITEMS = [
  { id: "big", name: "BigModel", kind: "repo", url: "https://github.com/o/big", description_zh: "大型单细胞基础模型", tags: ["基础模型"], related_tasks: ["pert"], related_paradigms: ["fm"], paper_ids: ["p1"], dataset_names: [], github: gh("o/big", 6200, true) },
  { id: "mid", name: "MidTool", kind: "tool", url: "https://github.com/o/mid", description_zh: "分析工具库", tags: ["scanpy"], related_tasks: ["state"], related_paradigms: [], paper_ids: [], dataset_names: [], github: gh("o/mid", 1500, true) },
  { id: "old", name: "OldRepo", kind: "repo", url: "https://github.com/o/old", description_zh: "久未更新", tags: [], related_tasks: ["pert"], related_paradigms: [], paper_ids: ["p1", "p2"], dataset_names: [], github: gh("o/old", 120, false, { archived: true }) },
  { id: "tiny", name: "tinyrepo", kind: "repo", url: "https://github.com/o/tiny", description_zh: "很小", tags: [], related_tasks: [], related_paradigms: [], paper_ids: [], dataset_names: [], github: gh("o/tiny", 12, null) },
  { id: "portal", name: "Cell Portal", kind: "data_portal", url: "https://example.org/p", description_zh: "数据门户，含 Replogle", tags: ["数据"], related_tasks: ["pert"], related_paradigms: [], paper_ids: [], dataset_names: ["Replogle K562", " replogle  k562 ", "scPerturb"], github: null },
  { id: "course", name: "Course", kind: "course", url: "https://example.org/c", description_zh: "课程", tags: [], related_tasks: [], related_paradigms: [], paper_ids: [], dataset_names: [], github: null }
];
const KINDS = [{ id: "repo", label: "仓库" }, { id: "tool", label: "工具" }, { id: "data_portal", label: "数据门户" }, { id: "course", label: "课程" }, { id: "podcast", label: "播客" }];
const ids = (l) => l.map((x) => x.id);

test("filterResources 无条件返回全部，非数组输入容错", () => {
  assert.equal(R.filterResources(ITEMS, {}).length, 6);
  assert.equal(R.filterResources(ITEMS).length, 6);
  assert.deepEqual(R.filterResources(null, {}), []);
  assert.deepEqual(R.filterResources(undefined, { kind: "repo" }), []);
  assert.deepEqual(R.filterResources([null, 3, ITEMS[0]], {}).map((x) => x.id), ["big"]);
});

test("filterResources 类型 / 任务 / 范式", () => {
  assert.deepEqual(ids(R.filterResources(ITEMS, { kind: "repo" })), ["big", "old", "tiny"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { task: "pert" })), ["big", "old", "portal"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { paradigm: "fm" })), ["big"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { kind: "repo", task: "pert" })), ["big", "old"]);
});

test("filterResources 维护状态只对有 github 指标且 maintained 为布尔的条目生效", () => {
  assert.deepEqual(ids(R.filterResources(ITEMS, { maintained: "yes" })), ["big", "mid"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { maintained: "no" })), ["old"]);
  assert.equal(R.filterResources(ITEMS, { maintained: "" }).length, 6);
});

test("filterResources star 档位边界", () => {
  assert.deepEqual(ids(R.filterResources(ITEMS, { stars: "ge5k" })), ["big"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { stars: "1k_5k" })), ["mid"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { stars: "100_1k" })), ["old"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { stars: "lt100" })), ["tiny"]);
  const edge = [{ id: "a", github: gh("a/a", 5000, true) }, { id: "b", github: gh("b/b", 4999, true) }, { id: "c", github: gh("c/c", 1000, true) }, { id: "d", github: gh("d/d", 999, true) }, { id: "e", github: gh("e/e", 100, true) }, { id: "f", github: gh("f/f", 99, true) }];
  assert.deepEqual(ids(R.filterResources(edge, { stars: "ge5k" })), ["a"]);
  assert.deepEqual(ids(R.filterResources(edge, { stars: "1k_5k" })), ["b", "c"]);
  assert.deepEqual(ids(R.filterResources(edge, { stars: "100_1k" })), ["d", "e"]);
  assert.deepEqual(ids(R.filterResources(edge, { stars: "lt100" })), ["f"]);
});

test("filterResources 搜索：名称/描述/标签/全名，不区分大小写，多词为与", () => {
  assert.deepEqual(ids(R.filterResources(ITEMS, { q: "BIGMODEL" })), ["big"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { q: "门户" })), ["portal"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { q: "SCANPY" })), ["mid"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { q: "o/old" })), ["old"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { q: "单细胞 基础" })), ["big"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { q: "单细胞 门户" })), []);
  assert.equal(R.filterResources(ITEMS, { q: "   " }).length, 6);
});

test("filterResources 多维度组合", () => {
  assert.deepEqual(ids(R.filterResources(ITEMS, { kind: "repo", maintained: "yes", stars: "ge5k", q: "big" })), ["big"]);
  assert.deepEqual(ids(R.filterResources(ITEMS, { kind: "repo", maintained: "yes", stars: "lt100" })), []);
  assert.deepEqual(ids(R.filterResources(ITEMS, { task: "pert", maintained: "no" })), ["old"]);
});

test("groupByKind 按 kinds 顺序、空组省略、组内 star 降序后名称", () => {
  const g = R.groupByKind(ITEMS, KINDS);
  assert.deepEqual(g.map((x) => x.id), ["repo", "tool", "data_portal", "course"]);
  assert.equal(g[0].label, "仓库");
  assert.deepEqual(ids(g[0].items), ["big", "old", "tiny"]);
  const mixed = [{ id: "z", name: "Zeta", kind: "repo", github: null }, { id: "a", name: "alpha", kind: "repo", github: null }, { id: "s", name: "S", kind: "repo", github: gh("o/s", 1, true) }];
  assert.deepEqual(ids(R.groupByKind(mixed, KINDS)[0].items), ["s", "a", "z"]);
});

test("groupByKind 未知类型归入「其他」，空输入容错", () => {
  const g = R.groupByKind([{ id: "u", name: "U", kind: "weird" }], KINDS);
  assert.equal(g.length, 1);
  assert.equal(g[0].label, "其他");
  assert.deepEqual(R.groupByKind(null, null), []);
  assert.deepEqual(R.groupByKind([], KINDS), []);
});

test("fmtStars", () => {
  assert.equal(R.fmtStars(0), "0");
  assert.equal(R.fmtStars(999), "999");
  assert.equal(R.fmtStars(1000), "1k");
  assert.equal(R.fmtStars(1234), "1.2k");
  assert.equal(R.fmtStars(1666), "1.7k");
  assert.equal(R.fmtStars(12345), "12k");
  assert.equal(R.fmtStars(null), "–");
  assert.equal(R.fmtStars(undefined), "–");
  assert.equal(R.fmtStars(NaN), "–");
});

test("buildPaperIndex / buildDatasetIndex", () => {
  const pi = R.buildPaperIndex(ITEMS);
  assert.deepEqual(ids(pi.p1), ["big", "old"]);
  assert.deepEqual(ids(pi.p2), ["old"]);
  assert.equal(pi.nope, undefined);
  const di = R.buildDatasetIndex(ITEMS);
  assert.deepEqual(ids(di["replogle k562"]), ["portal"]); // 大小写与空白归一后去重
  assert.deepEqual(ids(di["scperturb"]), ["portal"]);
  assert.equal(R.normName("  Replogle   K562 "), "replogle k562");
  assert.equal(di["constructor"], undefined);
});

test("索引构建对空输入容错", () => {
  assert.deepEqual(Object.keys(R.buildPaperIndex(null)), []);
  assert.deepEqual(Object.keys(R.buildDatasetIndex(undefined)), []);
  assert.deepEqual(Object.keys(R.buildDatasetIndex([null, { id: "x" }, { id: "y", dataset_names: [null, ""] }])), []);
});

test("groupBySection 按板块聚合、板块内按 kinds 顺序、未知 kind 进其他", () => {
  const kinds = [{ id: "lab" }, { id: "repo" }, { id: "tool" }, { id: "review" }];
  const sections = [{ id: "labs", label: "实验室", kinds: ["lab"] }, { id: "repos", label: "仓库", kinds: ["repo", "tool"] },
    { id: "empty", label: "空", kinds: ["review"] }];
  const items = [{ id: "t", name: "T", kind: "tool" }, { id: "r", name: "R", kind: "repo" },
    { id: "l", name: "L", kind: "lab" }, { id: "x", name: "X", kind: "weird" }];
  const g = R.groupBySection(items, sections, kinds);
  assert.deepEqual(g.map((x) => x.id), ["labs", "repos", "_other"]);
  assert.deepEqual(g[1].items.map((x) => x.id), ["r", "t"]);
  assert.deepEqual(R.groupBySection([], sections, kinds), []);
});
