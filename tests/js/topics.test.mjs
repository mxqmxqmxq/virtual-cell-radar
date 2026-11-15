import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const T = require(path.join(here, "../../src/aivc/web/assets/topics.js"));
const CASES = JSON.parse(fs.readFileSync(path.join(here, "../fixtures/term_match_cases.json"), "utf8"));

function memStore(initial) {
  const m = new Map(initial ? Object.entries(initial) : []);
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => void m.set(k, String(v)), m };
}
const KEY = "aivc.topics.v1";
const fresh = (s) => { T.init({ storage: s || memStore() }); return T; };

test("shared cases", async (t) => {
  assert.ok(CASES.cases.length >= 25);
  for (const c of CASES.cases) {
    await t.test(`${c.term} | ${c.note}`, () => {
      const re = T.compileTerm(c.term);
      assert.ok(re, "compiles");
      assert.equal(re.test(c.text), c.match);
      assert.equal(T.matchTerms(c.text, [c.term]), c.match);
    });
  }
});

test("compileTerm empty / non-string", () => {
  assert.equal(T.compileTerm(""), null);
  assert.equal(T.compileTerm("   "), null);
  assert.equal(T.compileTerm("**"), null);
  assert.equal(T.compileTerm(null), null);
});

test("regex metacharacters never throw or act as regex", () => {
  for (const term of ["(", "[", "a(b", "*(", "\\", "a{2,}", "^$", ".*", "(?<=x)y", "[a-z]+", "a\\d"]) {
    assert.doesNotThrow(() => T.compileTerm(term), term);
    assert.doesNotThrow(() => T.matchTerms("some text", [term]), term);
  }
  assert.equal(T.matchTerms("hello world", [".*"]), false);
  assert.equal(T.matchTerms("abc", ["[a-z]+"]), false);
  assert.equal(T.matchTerms("x [a-z]+ y", ["[a-z]+"]), true);
  assert.equal(T.matchTerms("a1", ["a\\d"]), false);
});

test("merged multi-term matching, CJK mixed with latin", () => {
  assert.equal(T.matchTerms("a Perturb-seq study", ["virtual cell", "perturb seq"]), true);
  assert.equal(T.matchTerms("研究空间转录组", ["virtual cell", "空间转录组"]), true);
  assert.equal(T.matchTerms("nothing", ["virtual cell", "空间转录组"]), false);
  assert.equal(T.matchTerms("", []), false);
});

test("paperText", () => {
  assert.equal(T.paperText({ title: "T", abstract: "A", keywords: ["k1", "k2"] }), "T\nA\nk1 ; k2");
  assert.equal(T.paperText({ title: "T" }), "T\n\n");
  assert.equal(T.paperText(null), "\n\n");
});

test("validateTopic: cleaning, dedupe, limits", () => {
  let v = T.validateTopic({ name: "  My\u0000 topic\n", terms: [" a b ", "A B", "ab\u0007c", "x"] });
  assert.equal(v.topic.name, "My topic");
  assert.deepEqual(v.topic.terms, ["a b", "abc"]);
  assert.equal(v.ok, false); // "x" too short
  assert.ok(v.errors.length === 1);

  v = T.validateTopic({ name: "n", terms: ["**", "*", "ok term"] });
  assert.deepEqual(v.topic.terms, ["ok term"]);
  assert.equal(v.ok, false);

  v = T.validateTopic({ name: "", terms: ["abc"] });
  assert.equal(v.ok, false);
  assert.equal(v.topic, null);
  v = T.validateTopic({ name: "x".repeat(41), terms: ["abc"] });
  assert.equal(v.ok, false);
  v = T.validateTopic({ name: "x".repeat(40), terms: ["abc"] });
  assert.equal(v.ok, true);
  v = T.validateTopic({ name: "n", terms: ["a".repeat(81)] });
  assert.equal(v.ok, false);
  v = T.validateTopic({ name: "n", terms: [] });
  assert.equal(v.ok, false);
  v = T.validateTopic({ name: "n", terms: Array.from({ length: 45 }, (_, i) => "term" + i) });
  assert.equal(v.topic.terms.length, 40);
  assert.equal(v.ok, false);
  v = T.validateTopic({ name: 5, terms: "abc" });
  assert.equal(v.ok, false);
  assert.doesNotThrow(() => T.validateTopic(undefined));
});

test("add / update / remove / enable, ids", () => {
  fresh();
  const r = T.addTopic({ name: "A", terms: ["alpha"] });
  assert.ok(r.ok);
  assert.notEqual(r.topic.id, "any");
  assert.equal(T.addTopic({ name: "", terms: ["alpha"] }).ok, false);
  assert.equal(T.activeTopics().length, 1);
  assert.ok(T.setEnabled(r.topic.id, false));
  assert.equal(T.activeTopics().length, 0);
  const u = T.updateTopic(r.topic.id, { name: "A2", terms: ["beta", "gamma"], enabled: true });
  assert.ok(u.ok);
  assert.equal(T.allTopics()[0].name, "A2");
  assert.equal(T.updateTopic(r.topic.id, { terms: ["x"] }).ok, false);
  assert.equal(T.allTopics()[0].terms.length, 2);
  assert.equal(T.updateTopic("nope", {}).ok, false);
  assert.ok(T.removeTopic(r.topic.id));
  assert.equal(T.removeTopic(r.topic.id), false);
});

test("max 20 user topics; unique ids", () => {
  fresh();
  const ids = new Set();
  for (let i = 0; i < 20; i++) {
    const r = T.addTopic({ name: "t" + i, terms: ["term" + i] });
    assert.ok(r.ok);
    ids.add(r.topic.id);
  }
  assert.equal(ids.size, 20);
  const r = T.addTopic({ name: "t21", terms: ["term21"] });
  assert.equal(r.ok, false);
  assert.equal(T.allTopics().length, 20);
});

test("presets: default_enabled, user override, id conflict", () => {
  fresh();
  T.setPresets([
    { id: "p1", name: "P1", terms: ["alpha"], default_enabled: true },
    { id: "p2", name: "P2", terms: ["beta"], default_enabled: false },
    { id: "any", name: "bad", terms: ["zzz"], default_enabled: true },
    { id: "p3", name: "", terms: ["zzz"] },
  ]);
  assert.deepEqual(T.getPresets().map((p) => p.id), ["p1", "p2"]);
  assert.deepEqual(T.activeTopics().map((t) => t.id), ["p1"]);
  assert.equal(T.activeTopics()[0].kind, "preset");
  T.setPresetEnabled("p1", false);
  T.setPresetEnabled("p2", true);
  assert.deepEqual(T.activeTopics().map((t) => t.id), ["p2"]);
  assert.equal(T.setPresetEnabled("nope", true), false);
  for (let i = 0; i < 5; i++) assert.ok(!["p1", "p2", "any"].includes(T.addTopic({ name: "u" + i, terms: ["uu" + i] }).topic.id));
});

test("storage round trip incl. preset prefs and preset id conflict", () => {
  const s = memStore();
  fresh(s);
  const a = T.addTopic({ name: "中文课题", terms: ["空间转录组", "spatial*"] });
  T.addTopic({ name: "off", terms: ["gamma"], enabled: false });
  T.setPresets([{ id: "p1", name: "P", terms: ["pp"], default_enabled: false }]);
  T.setPresetEnabled("p1", true);
  const stored = JSON.parse(s.getItem(KEY));
  assert.equal(stored.v, 1);
  assert.equal(stored.topics.length, 2);
  assert.deepEqual(stored.presets, { p1: true });
  assert.ok(!Number.isNaN(Date.parse(stored.updated)));

  T.init({ storage: s });
  T.setPresets([{ id: "p1", name: "P", terms: ["pp"], default_enabled: false }]);
  const all = T.allTopics();
  assert.equal(all.length, 3);
  assert.equal(all[0].id, a.topic.id);
  assert.equal(all[1].enabled, false);
  assert.equal(all[2].enabled, true);
});

test("corrupt / mismatched storage falls back to memory without throwing", () => {
  for (const raw of ["{not json", "null", "[]", '{"v":2,"topics":[]}', '{"v":1}', '{"v":1,"topics":"x"}', "", "12"]) {
    const s = memStore({ [KEY]: raw });
    assert.doesNotThrow(() => T.init({ storage: s }), raw);
    assert.equal(T.allTopics().length, 0, raw);
    assert.ok(T.addTopic({ name: "ok", terms: ["fine"] }).ok);
  }
  const broken = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); } };
  assert.doesNotThrow(() => T.init({ storage: broken }));
  assert.ok(T.addTopic({ name: "mem", terms: ["only"] }).ok);
  assert.equal(T.allTopics().length, 1);
  assert.doesNotThrow(() => T.init({ storage: null }));
  assert.ok(T.addTopic({ name: "mem", terms: ["only"] }).ok);
});

test("stored data is validated and clamped", () => {
  const topics = Array.from({ length: 30 }, (_, i) => ({ id: "id" + i, name: "n" + i, terms: ["term" + i], enabled: true }));
  topics.push({ id: "x", name: "bad", terms: [] }, { id: "any", name: "anyid", terms: ["abc"] }, { id: "id0", name: "N0", terms: ["dup"] });
  const s = memStore({ [KEY]: JSON.stringify({ v: 1, topics, presets: { a: true, b: "yes" } }) });
  T.init({ storage: s });
  const all = T.allTopics();
  assert.equal(all.length, 20);
  assert.ok(all.every((t) => t.id !== "any"));
  assert.equal(new Set(all.map((t) => t.id)).size, 20);
  const s2 = memStore({ [KEY]: JSON.stringify({ v: 1, topics: [{ id: "a b<script>", name: "n", terms: ["abc"] }, { id: "a", name: "n2", terms: ["a".repeat(100), "ok1"] }] }) });
  T.init({ storage: s2 });
  assert.equal(T.allTopics().length, 2);
  assert.match(T.allTopics()[0].id, /^[A-Za-z0-9_-]+$/);
  assert.deepEqual(T.allTopics()[1].terms, ["ok1"]);
});

test("matchPaper and cache invalidation", () => {
  fresh();
  const p = { title: "A virtual cell model", abstract: "about Perturb-seq", keywords: ["gene"] };
  assert.deepEqual(T.matchPaper(p), []);
  const a = T.addTopic({ name: "VC", terms: ["virtual cell"] });
  assert.deepEqual(T.matchPaper(p), [{ id: a.topic.id, name: "VC" }]);
  assert.strictEqual(T.matchPaper(p), T.matchPaper(p)); // cached
  const before = T.matchPaper(p);
  T.updateTopic(a.topic.id, { terms: ["nothing here"] });
  assert.deepEqual(T.matchPaper(p), []);
  T.updateTopic(a.topic.id, { terms: ["perturb seq"] });
  assert.equal(T.matchPaper(p).length, 1);
  T.setEnabled(a.topic.id, false);
  assert.deepEqual(T.matchPaper(p), []);
  T.setEnabled(a.topic.id, true);
  T.setPresets([{ id: "pk", name: "Kw", terms: ["gene"], default_enabled: true }]);
  assert.deepEqual(T.matchPaper(p).map((x) => x.id).sort(), [a.topic.id, "pk"].sort());
  assert.notStrictEqual(before, T.matchPaper(p));
  assert.deepEqual(T.matchPaper(null), []);
  assert.deepEqual(T.matchPaper({}), []);
});

test("countMatches", () => {
  const papers = [
    { title: "single-cell atlas", abstract: "" }, { title: "Graph nets", abstract: "for molecules" },
    { title: "x", abstract: "Single cell methods", keywords: ["a"] },
  ];
  assert.equal(T.countMatches(papers, ["single cell"]), 2);
  assert.equal(T.countMatches(papers, ["single cell", "graph"]), 3);
  assert.equal(T.countMatches(papers, []), 0);
  assert.equal(T.countMatches(null, ["a"]), 0);
});

test("performance: thousands of papers x 20 topics", () => {
  fresh();
  for (let i = 0; i < 20; i++) T.addTopic({ name: "t" + i, terms: ["alpha" + i, "beta gamma " + i, "delta*", "空间" + i, "ABC" + i] });
  const papers = Array.from({ length: 5000 }, (_, i) => ({ title: "Paper " + i + " on delta pathways", abstract: "lorem ipsum ".repeat(60), keywords: ["k"] }));
  const t0 = Date.now();
  let hits = 0;
  for (const p of papers) hits += T.matchPaper(p).length;
  assert.equal(hits, 5000 * 20);
  assert.ok(Date.now() - t0 < 3000);
});

test("onChange", () => {
  fresh();
  let n = 0;
  const off = T.onChange(() => { n++; });
  T.onChange(() => { throw new Error("boom"); });
  T.addTopic({ name: "a", terms: ["abc"] });
  assert.equal(n, 1);
  off();
  T.addTopic({ name: "b", terms: ["abc"] });
  assert.equal(n, 1);
  assert.equal(typeof T.onChange(null), "function");
});

test("share: round trip with Chinese", () => {
  fresh();
  const src = [{ name: "空间组学 Spatial", terms: ["空间转录组", "spatial transcriptom*", "C++"] }, { name: "GRN", terms: ["GRN inference"] }];
  const { hash, tooLong } = T.encodeShare(src);
  assert.equal(tooLong, false);
  assert.ok(hash.startsWith("#/topics?import="));
  assert.match(hash.slice("#/topics?import=".length), /^[A-Za-z0-9_-]+$/);
  const r = T.decodeShare(hash);
  assert.ok(r.ok);
  assert.deepEqual(r.topics, src);
  assert.deepEqual(T.decodeShare(hash.slice(1)).topics, src);
  assert.deepEqual(T.decodeShare(hash.split("import=")[1]).topics, src);
  assert.deepEqual(T.decodeShare(hash + "&x=1").topics, src);
  for (let n = 1; n <= 5; n++) { // padding variants
    const h = T.encodeShare([{ name: "n".repeat(n), terms: ["ab"] }]).hash;
    assert.deepEqual(T.decodeShare(h).topics[0].name, "n".repeat(n));
  }
});

test("share: default uses user topics; tooLong", () => {
  fresh();
  T.addTopic({ name: "mine", terms: ["abc"] });
  assert.equal(T.decodeShare(T.encodeShare().hash).topics[0].name, "mine");
  const big = Array.from({ length: 20 }, (_, i) => ({ name: "topic " + i, terms: Array.from({ length: 40 }, (_, j) => "some longer term " + i + "-" + j) }));
  assert.equal(T.encodeShare(big).tooLong, true);
});

test("decodeShare: hostile and malformed input never throws", () => {
  const b64 = (s) => Buffer.from(s, "utf8").toString("base64url");
  const hash = (s) => "#/topics?import=" + b64(s);
  const bad = [
    undefined, null, 5, {}, "", "#", "#/topics?import=", "#/topics?import=!!!!", "#/topics", "a", "aaaaa",
    hash("not json"), hash("null"), hash("[]"), hash('"str"'), hash("42"),
    hash('{"v":2,"topics":[]}'), hash('{"v":1}'), hash('{"v":1,"topics":"x"}'), hash('{"v":1,"topics":[]}'),
    hash('{"v":1,"topics":[1,null,"s",[]]}'), hash('{"v":1,"topics":[{"name":"a"}]}'),
    hash('{"v":1,"topics":[{"name":"","terms":["abc"]}]}'),
    "#/topics?import=" + "A".repeat(300000),
    "#/topics?import=" + Buffer.from([0xff, 0xfe, 0xfd]).toString("base64url"),
  ];
  for (const x of bad) {
    let r;
    assert.doesNotThrow(() => { r = T.decodeShare(x); });
    assert.equal(r.ok, false, String(x).slice(0, 50));
    assert.deepEqual(r.topics, []);
    assert.ok(Array.isArray(r.errors));
  }
});

test("decodeShare: hostile payload is clamped and inert", () => {
  globalThis.__pwned = false;
  const payload = {
    v: 1,
    topics: [
      { name: "<img src=x onerror=alert(1)>", terms: ["__import__('os')", "globalThis.__pwned=true", "(?:a+)+$", "ok term", "x"], __proto__: { polluted: 1 }, extra: "ignored" },
      { name: "dup", terms: ["abc"] }, { name: "DUP", terms: ["abc"] },
      ...Array.from({ length: 40 }, (_, i) => ({ name: "n" + i, terms: ["term" + i] })),
    ],
  };
  const text = JSON.stringify(payload).replace('"extra"', '"__proto__":{"polluted":true},"extra"');
  const r = T.decodeShare("#/topics?import=" + Buffer.from(text).toString("base64url"));
  assert.ok(r.ok);
  assert.equal(r.topics.length, 20);
  assert.equal(globalThis.__pwned, false);
  assert.equal({}.polluted, undefined);
  assert.deepEqual(Object.keys(r.topics[0]).sort(), ["name", "terms"]);
  assert.equal(r.topics[0].name, "<img src=x onerror=alert(1)>"); // data only; UI must use textContent
  assert.ok(r.topics[0].terms.length <= 40);
  assert.equal(r.topics.filter((t) => t.name.toLowerCase() === "dup").length, 1);
  assert.ok(r.errors.length > 0);
  assert.doesNotThrow(() => T.matchTerms("globalThis.__pwned=true (?:a+)+$", r.topics[0].terms));
  assert.equal(globalThis.__pwned, false);
});

test("export / import JSON and mergeImported", () => {
  fresh();
  T.addTopic({ name: "One", terms: ["alpha", "beta"] });
  T.addTopic({ name: "二", terms: ["中文词"] });
  const text = T.exportJSON();
  const parsed = JSON.parse(text);
  assert.equal(parsed.v, 1);
  assert.equal(parsed.topics.length, 2);
  const imp = T.importJSON(text);
  assert.ok(imp.ok);
  assert.equal(T.mergeImported(imp.topics), 0); // all dupes
  assert.equal(T.mergeImported([{ name: "ONE", terms: ["zzz"] }, { name: "Three", terms: ["gamma"] }, { name: "", terms: ["q"] }, null]), 1);
  assert.equal(T.allTopics().length, 3);
  for (const bad of ["", "{", "null", "[]", '{"v":1,"topics":5}', null, 3]) {
    assert.equal(T.importJSON(bad).ok, false);
  }
  assert.equal(T.mergeImported("x"), 0);
});

test("mergeImported respects 20 limit", () => {
  fresh();
  for (let i = 0; i < 18; i++) T.addTopic({ name: "t" + i, terms: ["term" + i] });
  const extra = Array.from({ length: 5 }, (_, i) => ({ name: "new" + i, terms: ["newterm" + i] }));
  assert.equal(T.mergeImported(extra), 2);
  assert.equal(T.allTopics().length, 20);
});

test("source hygiene", () => {
  const src = fs.readFileSync(path.join(here, "../../src/aivc/web/assets/topics.js"), "utf8");
  assert.ok(!/\(\?<[=!]/.test(src.replace(/\/\*[\s\S]*?\*\//, "")), "no lookbehind");
  assert.ok(!/innerHTML|\beval\s*\(|new Function/.test(src.replace(/\/\*[\s\S]*?\*\//, "")));
});
