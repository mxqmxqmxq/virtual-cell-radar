/*
 * topics.js — 「我的课题」核心库（纯逻辑，无界面，无依赖）。
 * 浏览器里挂 window.AIVCTopics；Node 下 module.exports 同一对象。
 *
 * 匹配规则 v1，与 src/aivc/termmatch.py 一致（共享用例 tests/fixtures/term_match_cases.json）：
 *   文本 = title + "\n" + abstract + "\n" + keywords.join(" ; ")；课题任一词项命中即命中。
 *   词项去首尾空格；含 CJK（U+4E00-U+9FFF）按字面子串、区分大小写；
 *   否则不区分大小写，空格与连字符等价；结尾 * 为前缀匹配，开头 * 允许前接字母；
 *   全大写且不含空格的缩写不加复数，其余自动可选 s/es；
 *   命中处前面不能紧挨 [\w-]，后面不能紧挨 \w。
 *   已知差异：JS 的 \w 只含 ASCII，Python 的 \w 含 Unicode；词项紧邻重音字母时两边结果可能不同。
 *   词项一律先转义再拼接，用户输入绝不会被当作正则；本文件不使用 lookbehind / innerHTML / eval。
 *
 * API（所有函数不抛异常）
 *   LIMITS                      {maxTopics:20, nameMin:1, nameMax:40, maxTerms:40, termMin:2, termMax:80, maxShareHash:4000}
 *   compileTerm(term)           -> RegExp | null（单个词项；CJK 词项区分大小写，其余不区分）
 *   paperText(p)                -> string（title + "\n" + abstract + "\n" + keywords 以 " ; " 连接）
 *   matchTerms(text, terms)     -> bool（terms 为字符串数组，任一命中）
 *   validateTopic({name,terms}) -> {ok, topic:{name,terms}|null, errors:[string]}
 *                                  topic 是清洗后的结果（去控制字符与首尾空白、不区分大小写去重、
 *                                  非法/超限词项被丢弃并记入 errors）；ok 为 true 当且仅当 errors 为空。
 *                                  名称非法或没有任何合法词项时 topic 为 null。
 *   init({storage})             初始化/重置状态；storage 默认 window.localStorage，需有 getItem/setItem；
 *                               不可用、内容损坏或版本不符则退回内存状态。键 "aivc.topics.v1"，
 *                               值 {"v":1,"topics":[{id,name,terms,enabled}],"presets":{id:bool},"updated":ISO}
 *   setPresets(list)            list 来自 site/data/topics.json 的 presets，项 {id,name,terms,default_enabled}
 *   setPresetEnabled(id, bool)  用户设置优先，否则取 default_enabled
 *   getPresets()                -> [{id,name,terms,enabled,default_enabled,kind:"preset"}]
 *   addTopic({name,terms,enabled?})          -> {ok, topic, errors}（严格：有任何错误则不保存；
 *                                              超过 20 个用户课题则 ok:false）
 *   updateTopic(id, {name?,terms?,enabled?}) -> {ok, topic, errors}（严格，同上）
 *   removeTopic(id) -> bool；  setEnabled(id, bool) -> bool（id 可为用户课题或预设）
 *   activeTopics()              -> [{id,name,terms,kind:"user"|"preset"}] 当前启用的用户课题 + 预设
 *   allTopics()                 -> [{id,name,terms,kind,enabled}] 全部（用户课题在前，预设在后）
 *   matchPaper(p)               -> [{id,name}] 命中的启用课题；按状态版本号缓存，课题一变全部失效
 *   countMatches(papers, terms) -> number（编辑课题时实时显示命中篇数；terms 为字符串数组）
 *   encodeShare(topics?)        -> {hash, tooLong}；topics 缺省为全部用户课题；
 *                                  hash 形如 "#/topics?import=<UTF-8 JSON 的 base64url>"，超过 4000 字符 tooLong:true
 *   decodeShare(str)            -> {ok, topics:[{name,terms}], errors}；str 可为完整 hash、"import=..." 或裸 base64url；
 *                                  输入视为不可信数据，只做 JSON 解析与结构校验，超限部分裁剪
 *   exportJSON()                -> string（文件 aivc-topics.json 的内容，载荷同分享）
 *   importJSON(text)            -> {ok, topics, errors}（只解析校验，不并入）
 *   mergeImported(topics)       -> number（按名称不区分大小写去重并入，受 20 个上限约束，返回实际加入数）
 *   onChange(fn)                -> 取消订阅函数；任何状态变化后调用 fn()
 *   getVersion()                -> number（状态版本号，每次变化加一）
 */
(function (root) {
  "use strict";

  var LIMITS = {
    maxTopics: 20, nameMin: 1, nameMax: 40, maxTerms: 40, termMin: 2, termMax: 80, maxShareHash: 4000
  };
  var STORE_KEY = "aivc.topics.v1";
  var SHARE_PREFIX = "#/topics?import=";
  var MAX_SHARE_INPUT = 200000;

  var CJK = /[\u4e00-\u9fff]/;
  var CONTROL = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g;

  function isStr(x) { return typeof x === "string"; }
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\\/\-]/g, "\\$&"); }

  /* ---------------- 词项编译 ---------------- */

  function isAcronym(term) {
    var letters = 0, i, c;
    for (i = 0; i < term.length; i++) {
      c = term.charAt(i);
      if (c.toLowerCase() !== c.toUpperCase()) {
        letters++;
        if (c !== c.toUpperCase()) return false;
      }
    }
    return letters > 0 && term.trim().indexOf(" ") < 0;
  }

  /* 返回 {cjk:bool, src:string}；CJK 为纯转义字面量，其余为带前缀/后缀的主体（不含前后边界）。 */
  function termSource(term) {
    if (!isStr(term)) return null;
    term = term.trim();
    if (!term) return null;
    if (CJK.test(term)) return { cjk: true, src: esc(term) };
    var wildcard = term.charAt(term.length - 1) === "*";
    var lead = term.charAt(0) === "*" ? "\\w*" : "";
    term = term.replace(/^\*+|\*+$/g, "");
    var parts = term.split(/[\s\-]+/).filter(function (x) { return x; }).map(esc);
    if (!parts.length) return null;
    var body = parts.join("[\\s\\-]+");
    var suffix = wildcard ? "\\w*" : (isAcronym(term) ? "" : "(?:s|es)?");
    return { cjk: false, src: lead + body + suffix };
  }

  function wrapPlain(src) { return "(?:^|[^\\w\\-])(?:" + src + ")(?!\\w)"; }

  function compileTerm(term) {
    var t = termSource(term);
    if (!t) return null;
    try {
      return t.cjk ? new RegExp(t.src) : new RegExp(wrapPlain(t.src), "i");
    } catch (e) { return null; }
  }

  /* 把多个词项合并成至多两条正则：非 CJK（不区分大小写）与 CJK（区分大小写）。 */
  function compileMany(terms) {
    var plain = [], cjk = [], i, t, out = [];
    terms = Array.isArray(terms) ? terms : [];
    for (i = 0; i < terms.length; i++) {
      t = termSource(terms[i]);
      if (!t) continue;
      (t.cjk ? cjk : plain).push(t.src);
    }
    try {
      if (plain.length) out.push(new RegExp(wrapPlain(plain.join("|")), "i"));
      if (cjk.length) out.push(new RegExp(cjk.join("|")));
    } catch (e) { return []; }
    return out;
  }

  function testAll(res, text) {
    for (var i = 0; i < res.length; i++) if (res[i].test(text)) return true;
    return false;
  }

  function paperText(p) {
    p = p || {};
    var kw = Array.isArray(p.keywords) ? p.keywords.filter(isStr) : [];
    return (isStr(p.title) ? p.title : "") + "\n" + (isStr(p.abstract) ? p.abstract : "") + "\n" + kw.join(" ; ");
  }

  function matchTerms(text, terms) {
    return testAll(compileMany(terms), isStr(text) ? text : "");
  }

  /* ---------------- 校验 ---------------- */

  function cleanStr(s) { return s.replace(CONTROL, "").trim(); }

  function validateTopic(input) {
    var errors = [], name = "", terms = [], seen = {};
    input = input && typeof input === "object" ? input : {};
    if (!isStr(input.name)) errors.push("课题名称缺失");
    else {
      name = cleanStr(input.name);
      if (name.length < LIMITS.nameMin) errors.push("课题名称不能为空");
      else if (name.length > LIMITS.nameMax) { errors.push("课题名称最多 " + LIMITS.nameMax + " 个字符"); name = ""; }
    }
    var raw = Array.isArray(input.terms) ? input.terms : null;
    if (!raw) errors.push("词项列表缺失");
    else {
      for (var i = 0; i < raw.length; i++) {
        if (!isStr(raw[i])) { errors.push("词项必须是文本"); continue; }
        var t = cleanStr(raw[i]);
        if (!t) continue;
        if (t.length < LIMITS.termMin || t.length > LIMITS.termMax) {
          errors.push("词项「" + t.slice(0, 20) + "」长度需在 " + LIMITS.termMin + "–" + LIMITS.termMax + " 个字符之间");
          continue;
        }
        if (!t.replace(/^\*+|\*+$/g, "").trim()) { errors.push("词项「" + t + "」去掉 * 后为空"); continue; }
        var key = t.toLowerCase();
        if (seen[key] === true) continue;
        if (terms.length >= LIMITS.maxTerms) { errors.push("词项最多 " + LIMITS.maxTerms + " 个，多余的已忽略"); break; }
        seen[key] = true;
        terms.push(t);
      }
      if (!terms.length) errors.push("至少需要一个有效词项");
    }
    return { ok: errors.length === 0, topic: name && terms.length ? { name: name, terms: terms } : null, errors: errors };
  }

  /* ---------------- 状态与存储 ---------------- */

  var state, storage = null, listeners = [], cache;

  function resetState() {
    state = { topics: [], presetPrefs: {}, presets: [], version: 0 };
    cache = { ver: -1, topics: null, papers: null };
  }
  resetState();

  function isOwn(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

  function presetIds() {
    var m = {};
    state.presets.forEach(function (p) { m[p.id] = true; });
    return m;
  }

  function genId() {
    var taken = presetIds(), id;
    state.topics.forEach(function (t) { taken[t.id] = true; });
    do {
      id = "u-" + Date.now().toString(36).slice(-4) + Math.floor(Math.random() * 1679616).toString(36);
    } while (taken[id] === true || id === "any");
    return id;
  }

  function validId(id) { return isStr(id) && /^[A-Za-z0-9_\-]{1,40}$/.test(id) && id !== "any"; }

  function bump(persist) {
    state.version++;
    if (persist !== false) save();
    var ls = listeners.slice();
    for (var i = 0; i < ls.length; i++) { try { ls[i](); } catch (e) { /* 订阅者异常不影响状态 */ } }
  }

  function save() {
    if (!storage) return;
    try {
      storage.setItem(STORE_KEY, JSON.stringify({
        v: 1,
        topics: state.topics.map(function (t) { return { id: t.id, name: t.name, terms: t.terms.slice(), enabled: !!t.enabled }; }),
        presets: state.presetPrefs,
        updated: new Date().toISOString()
      }));
    } catch (e) { /* 存储不可用：仅保留内存状态 */ }
  }

  function load() {
    var raw, data;
    try { raw = storage.getItem(STORE_KEY); } catch (e) { return; }
    if (!isStr(raw) || !raw) return;
    try { data = JSON.parse(raw); } catch (e) { return; }
    if (!data || typeof data !== "object" || data.v !== 1 || !Array.isArray(data.topics)) return;
    var seenIds = {}, seenNames = {};
    data.topics.slice(0, LIMITS.maxTopics * 2).forEach(function (t) {
      if (state.topics.length >= LIMITS.maxTopics || !t || typeof t !== "object") return;
      var v = validateTopic(t);
      if (!v.topic) return;
      var nk = v.topic.name.toLowerCase();
      if (seenNames[nk] === true) return;
      seenNames[nk] = true;
      var id = validId(t.id) && seenIds[t.id] !== true ? t.id : genId();
      seenIds[id] = true;
      state.topics.push({ id: id, name: v.topic.name, terms: v.topic.terms, enabled: t.enabled !== false });
    });
    var pr = data.presets;
    if (pr && typeof pr === "object" && !Array.isArray(pr)) {
      Object.keys(pr).slice(0, 200).forEach(function (k) {
        if (typeof pr[k] === "boolean" && k.length <= 80) state.presetPrefs[k] = pr[k];
      });
    }
  }

  function init(opts) {
    resetState();
    storage = null;
    var s = opts && opts.storage;
    if (!s && !(opts && isOwn(opts, "storage"))) {
      try { s = root && root.localStorage; } catch (e) { s = null; }
    }
    if (s && typeof s.getItem === "function" && typeof s.setItem === "function") {
      storage = s;
      load();
    }
    bump(false);
    return api;
  }

  /* ---------------- 预设 ---------------- */

  function setPresets(list) {
    var out = [], seen = {};
    (Array.isArray(list) ? list : []).forEach(function (p) {
      if (!p || typeof p !== "object" || !validId(p.id) || seen[p.id] === true) return;
      var v = validateTopic({ name: p.name, terms: p.terms });
      if (!v.topic) return;
      seen[p.id] = true;
      out.push({ id: p.id, name: v.topic.name, terms: v.topic.terms, default_enabled: p.default_enabled === true });
    });
    state.presets = out;
    // 用户课题 id 若与预设冲突则重新生成
    state.topics.forEach(function (t) { if (seen[t.id] === true) t.id = genId(); });
    bump();
  }

  function presetEnabled(p) {
    return isOwn(state.presetPrefs, p.id) ? !!state.presetPrefs[p.id] : p.default_enabled;
  }

  function setPresetEnabled(id, on) {
    if (presetIds()[id] !== true) return false;
    state.presetPrefs[id] = !!on;
    bump();
    return true;
  }

  function getPresets() {
    return state.presets.map(function (p) {
      return { id: p.id, name: p.name, terms: p.terms.slice(), enabled: presetEnabled(p), default_enabled: p.default_enabled, kind: "preset" };
    });
  }

  /* ---------------- 用户课题 ---------------- */

  function findUser(id) {
    for (var i = 0; i < state.topics.length; i++) if (state.topics[i].id === id) return state.topics[i];
    return null;
  }

  function pub(t) { return { id: t.id, name: t.name, terms: t.terms.slice(), enabled: !!t.enabled, kind: "user" }; }

  function addTopic(input) {
    var v = validateTopic(input);
    if (state.topics.length >= LIMITS.maxTopics) {
      v.errors.push("最多只能有 " + LIMITS.maxTopics + " 个课题");
      return { ok: false, topic: null, errors: v.errors };
    }
    if (!v.ok) return { ok: false, topic: null, errors: v.errors };
    var t = { id: genId(), name: v.topic.name, terms: v.topic.terms, enabled: !(input && input.enabled === false) };
    state.topics.push(t);
    bump();
    return { ok: true, topic: pub(t), errors: [] };
  }

  function updateTopic(id, patch) {
    var t = findUser(id);
    if (!t) return { ok: false, topic: null, errors: ["课题不存在"] };
    patch = patch && typeof patch === "object" ? patch : {};
    var v = validateTopic({
      name: isOwn(patch, "name") ? patch.name : t.name,
      terms: isOwn(patch, "terms") ? patch.terms : t.terms
    });
    if (!v.ok) return { ok: false, topic: pub(t), errors: v.errors };
    t.name = v.topic.name;
    t.terms = v.topic.terms;
    if (typeof patch.enabled === "boolean") t.enabled = patch.enabled;
    bump();
    return { ok: true, topic: pub(t), errors: [] };
  }

  function removeTopic(id) {
    for (var i = 0; i < state.topics.length; i++) {
      if (state.topics[i].id === id) { state.topics.splice(i, 1); bump(); return true; }
    }
    return false;
  }

  function setEnabled(id, on) {
    var t = findUser(id);
    if (t) { t.enabled = !!on; bump(); return true; }
    return setPresetEnabled(id, on);
  }

  function allTopics() {
    return state.topics.map(pub).concat(getPresets());
  }

  function activeTopics() {
    return allTopics().filter(function (t) { return t.enabled; }).map(function (t) {
      return { id: t.id, name: t.name, terms: t.terms, kind: t.kind };
    });
  }

  /* ---------------- 匹配 ---------------- */

  function ensureCache() {
    if (cache.ver === state.version) return;
    cache = {
      ver: state.version,
      papers: typeof WeakMap === "function" ? new WeakMap() : null,
      topics: activeTopics().map(function (t) { return { id: t.id, name: t.name, res: compileMany(t.terms) }; })
    };
  }

  function matchPaper(p) {
    if (!p || typeof p !== "object") return [];
    ensureCache();
    if (cache.papers) {
      var hit = cache.papers.get(p);
      if (hit) return hit;
    }
    var text = paperText(p), out = [];
    for (var i = 0; i < cache.topics.length; i++) {
      var t = cache.topics[i];
      if (testAll(t.res, text)) out.push({ id: t.id, name: t.name });
    }
    if (cache.papers) cache.papers.set(p, out);
    return out;
  }

  function countMatches(papers, terms) {
    var res = compileMany(terms), n = 0;
    if (!res.length || !Array.isArray(papers)) return 0;
    for (var i = 0; i < papers.length; i++) if (testAll(res, paperText(papers[i]))) n++;
    return n;
  }

  /* ---------------- 分享 / 导入导出 ---------------- */

  var B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

  function utf8Bytes(str) {
    if (typeof TextEncoder === "function") return Array.prototype.slice.call(new TextEncoder().encode(str));
    var s = unescape(encodeURIComponent(str)), out = [];
    for (var i = 0; i < s.length; i++) out.push(s.charCodeAt(i));
    return out;
  }

  function utf8Decode(bytes) {
    if (typeof TextDecoder === "function") return new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(bytes));
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return decodeURIComponent(escape(s));
  }

  function b64urlEncode(bytes) {
    var out = "", i, n;
    for (i = 0; i < bytes.length; i += 3) {
      n = (bytes[i] << 16) | ((bytes[i + 1] || 0) << 8) | (bytes[i + 2] || 0);
      out += B64.charAt((n >> 18) & 63) + B64.charAt((n >> 12) & 63);
      if (i + 1 < bytes.length) out += B64.charAt((n >> 6) & 63);
      if (i + 2 < bytes.length) out += B64.charAt(n & 63);
    }
    return out;
  }

  function b64urlDecode(s) {
    if (!/^[A-Za-z0-9_\-]*$/.test(s) || s.length % 4 === 1) return null;
    var bytes = [], i, n, a, b, c, d;
    for (i = 0; i < s.length; i += 4) {
      a = B64.indexOf(s.charAt(i)); b = B64.indexOf(s.charAt(i + 1));
      c = i + 2 < s.length ? B64.indexOf(s.charAt(i + 2)) : -1;
      d = i + 3 < s.length ? B64.indexOf(s.charAt(i + 3)) : -1;
      n = (a << 18) | (b << 12) | ((c < 0 ? 0 : c) << 6) | (d < 0 ? 0 : d);
      bytes.push((n >> 16) & 255);
      if (c >= 0) bytes.push((n >> 8) & 255);
      if (d >= 0) bytes.push(n & 255);
    }
    return bytes;
  }

  function payloadOf(topics) {
    var list = Array.isArray(topics) ? topics : state.topics;
    return {
      v: 1,
      topics: list.filter(function (t) { return t && isStr(t.name) && Array.isArray(t.terms); })
        .map(function (t) { return { name: t.name, terms: t.terms.filter(isStr) }; })
    };
  }

  function encodeShare(topics) {
    var hash = SHARE_PREFIX + b64urlEncode(utf8Bytes(JSON.stringify(payloadOf(topics))));
    return { hash: hash, tooLong: hash.length > LIMITS.maxShareHash };
  }

  /* 把不可信的已解析对象整理成安全课题列表。 */
  function sanitizePayload(data) {
    var errors = [], topics = [], seen = {};
    if (!data || typeof data !== "object" || Array.isArray(data)) return { ok: false, topics: [], errors: ["内容不是有效的课题数据"] };
    if (data.v !== 1) return { ok: false, topics: [], errors: ["不支持的数据版本"] };
    if (!Array.isArray(data.topics)) return { ok: false, topics: [], errors: ["缺少课题列表"] };
    data.topics.forEach(function (t, i) {
      if (topics.length >= LIMITS.maxTopics) {
        if (i === LIMITS.maxTopics) errors.push("课题最多 " + LIMITS.maxTopics + " 个，多余的已忽略");
        return;
      }
      if (!t || typeof t !== "object" || Array.isArray(t)) { errors.push("第 " + (i + 1) + " 项不是课题"); return; }
      var v = validateTopic({ name: t.name, terms: t.terms });
      v.errors.forEach(function (e) { errors.push("第 " + (i + 1) + " 项：" + e); });
      if (!v.topic) return;
      var k = v.topic.name.toLowerCase();
      if (seen[k] === true) { errors.push("第 " + (i + 1) + " 项名称重复，已忽略"); return; }
      seen[k] = true;
      topics.push(v.topic);
    });
    if (!topics.length && !errors.length) errors.push("没有可导入的课题");
    return { ok: topics.length > 0, topics: topics, errors: errors };
  }

  function decodeShare(str) {
    try {
      if (!isStr(str)) return { ok: false, topics: [], errors: ["分享链接无效"] };
      if (str.length > MAX_SHARE_INPUT) return { ok: false, topics: [], errors: ["分享内容过长"] };
      var s = str.trim(), m = /(?:^|[?&#])import=([^&#]*)/.exec(s);
      if (m) s = m[1];
      else if (s.charAt(0) === "#") return { ok: false, topics: [], errors: ["分享链接无效"] };
      var bytes = b64urlDecode(s);
      if (!bytes) return { ok: false, topics: [], errors: ["分享链接无效"] };
      return sanitizePayload(JSON.parse(utf8Decode(bytes)));
    } catch (e) {
      return { ok: false, topics: [], errors: ["分享链接无法解析"] };
    }
  }

  function exportJSON() { return JSON.stringify(payloadOf(), null, 2); }

  function importJSON(text) {
    try {
      if (!isStr(text) || text.length > MAX_SHARE_INPUT) return { ok: false, topics: [], errors: ["文件内容无效或过大"] };
      return sanitizePayload(JSON.parse(text));
    } catch (e) {
      return { ok: false, topics: [], errors: ["文件不是有效的 JSON"] };
    }
  }

  function mergeImported(topics) {
    var added = 0, names = {};
    state.topics.forEach(function (t) { names[t.name.toLowerCase()] = true; });
    (Array.isArray(topics) ? topics : []).forEach(function (t) {
      if (state.topics.length >= LIMITS.maxTopics || !t || typeof t !== "object") return;
      var v = validateTopic({ name: t.name, terms: t.terms });
      if (!v.topic || names[v.topic.name.toLowerCase()] === true) return;
      names[v.topic.name.toLowerCase()] = true;
      state.topics.push({ id: genId(), name: v.topic.name, terms: v.topic.terms, enabled: true });
      added++;
    });
    if (added) bump();
    return added;
  }

  function onChange(fn) {
    if (typeof fn !== "function") return function () {};
    listeners.push(fn);
    return function () {
      var i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    };
  }

  var api = {
    LIMITS: LIMITS,
    compileTerm: compileTerm, paperText: paperText, matchTerms: matchTerms,
    validateTopic: validateTopic,
    init: init, setPresets: setPresets, setPresetEnabled: setPresetEnabled, getPresets: getPresets,
    addTopic: addTopic, updateTopic: updateTopic, removeTopic: removeTopic, setEnabled: setEnabled,
    activeTopics: activeTopics, allTopics: allTopics,
    matchPaper: matchPaper, countMatches: countMatches,
    encodeShare: encodeShare, decodeShare: decodeShare,
    exportJSON: exportJSON, importJSON: importJSON, mergeImported: mergeImported,
    onChange: onChange, getVersion: function () { return state.version; }
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else if (root) {
    root.AIVCTopics = api;
    init();
  }
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this));
