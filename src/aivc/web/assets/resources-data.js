/*
 * resources-data.js — 知识资源页的纯函数（无界面、无依赖）。
 * 浏览器里挂 window.AIVCResourcesData；Node 下 module.exports 同一对象。
 *
 *   filterResources(items, f)   f: {kind, task, paradigm, maintained, stars, q}
 *                               maintained: yes | no | ""（仅对有 github 指标的条目生效，其余条目在 yes/no 下被排除）
 *                               stars: ge5k | 1k_5k | 100_1k | lt100（同上，仅对有 github 指标的条目生效）
 *                               q: 在名称、描述、标签、github 全名里做不区分大小写的搜索，多词为与
 *   groupByKind(items, kinds)   -> [{id,label,items}]，按 kinds 顺序；空组省略；
 *                               组内有 star 的按 star 降序在前，其余按名称；未知类型归入末尾「其他」
 *   fmtStars(n)                 -> "1.2k" / "12k" / "999" / "–"
 *   buildPaperIndex(items)      -> {论文 id: [资源…]}
 *   buildDatasetIndex(items)    -> {数据集名（小写归一）: [资源…]}
 *   normName(s)                 数据集名归一：去首尾空白、压缩空白、转小写
 *   STAR_BUCKETS                star 档位 [{id,label}]
 */
(function (root) {
  "use strict";

  var STAR_BUCKETS = [
    { id: "ge5k", label: "≥ 5k" },
    { id: "1k_5k", label: "1k – 5k" },
    { id: "100_1k", label: "100 – 1k" },
    { id: "lt100", label: "< 100" }
  ];

  function arr(x) { return Array.isArray(x) ? x : []; }
  function str(x) { return typeof x === "string" ? x : (x == null ? "" : String(x)); }
  function gh(it) { return it && it.github && typeof it.github === "object" ? it.github : null; }
  function starsOf(it) {
    var g = gh(it);
    return g && typeof g.stars === "number" && isFinite(g.stars) ? g.stars : null;
  }

  function inBucket(n, bucket) {
    if (n === null) return false;
    switch (bucket) {
      case "ge5k": return n >= 5000;
      case "1k_5k": return n >= 1000 && n < 5000;
      case "100_1k": return n >= 100 && n < 1000;
      case "lt100": return n < 100;
      default: return true;
    }
  }

  function haystack(it) {
    var g = gh(it);
    return [str(it.name), str(it.description_zh), arr(it.tags).map(str).join(" "), g ? str(g.full_name) : ""].join("\n").toLowerCase();
  }

  function filterResources(items, f) {
    f = f || {};
    var terms = str(f.q).toLowerCase().split(/\s+/).filter(Boolean);
    return arr(items).filter(function (it) {
      if (!it || typeof it !== "object") return false;
      if (f.kind && it.kind !== f.kind) return false;
      if (f.task && arr(it.related_tasks).indexOf(f.task) < 0) return false;
      if (f.paradigm && arr(it.related_paradigms).indexOf(f.paradigm) < 0) return false;
      if (f.maintained === "yes" || f.maintained === "no") {
        var g = gh(it);
        if (!g || typeof g.maintained !== "boolean") return false;
        if (g.maintained !== (f.maintained === "yes")) return false;
      }
      if (f.stars && !inBucket(starsOf(it), f.stars)) return false;
      if (terms.length) {
        var h = haystack(it);
        for (var i = 0; i < terms.length; i++) if (h.indexOf(terms[i]) < 0) return false;
      }
      return true;
    });
  }

  function cmpName(a, b) {
    var x = str(a.name).toLowerCase(), y = str(b.name).toLowerCase();
    return x < y ? -1 : x > y ? 1 : 0;
  }
  function cmpWithin(a, b) {
    var sa = starsOf(a), sb = starsOf(b);
    if (sa !== null && sb !== null) return sb - sa || cmpName(a, b);
    if (sa !== null) return -1;
    if (sb !== null) return 1;
    return cmpName(a, b);
  }

  function groupByKind(items, kinds) {
    var groups = [], byKind = {};
    arr(kinds).forEach(function (k) {
      if (!k || !k.id || byKind[k.id]) return;
      var g = { id: k.id, label: str(k.label) || k.id, items: [] };
      byKind[k.id] = g; groups.push(g);
    });
    var extra = null;
    arr(items).forEach(function (it) {
      if (!it) return;
      var g = byKind[it.kind];
      if (!g) {
        if (!extra) { extra = { id: "_other", label: "其他", items: [] }; groups.push(extra); }
        g = extra;
      }
      g.items.push(it);
    });
    groups.forEach(function (g) { g.items.sort(cmpWithin); });
    return groups.filter(function (g) { return g.items.length > 0; });
  }

  // 按页面板块分组：sections=[{id,label,kinds:[...]}]；板块内先按 kinds 顺序、再按组内规则排序；未归入任何板块的 kind 进「其他」
  function groupBySection(items, sections, kinds) {
    var groups = [], bySec = {}, kindSec = {}, kindRank = {};
    arr(kinds).forEach(function (k, i) { if (k && k.id) kindRank[k.id] = i; });
    arr(sections).forEach(function (sec) {
      if (!sec || !sec.id || bySec[sec.id]) return;
      var g = { id: sec.id, label: str(sec.label) || sec.id, items: [] };
      bySec[sec.id] = g; groups.push(g);
      arr(sec.kinds).forEach(function (k) { if (!kindSec[k]) kindSec[k] = g; });
    });
    var extra = null;
    arr(items).forEach(function (it) {
      if (!it) return;
      var g = kindSec[it.kind];
      if (!g) {
        if (!extra) { extra = { id: "_other", label: "其他", items: [] }; groups.push(extra); }
        g = extra;
      }
      g.items.push(it);
    });
    function rank(it) { return Object.prototype.hasOwnProperty.call(kindRank, it.kind) ? kindRank[it.kind] : 999; }
    groups.forEach(function (g) { g.items.sort(function (a, b) { return (rank(a) - rank(b)) || cmpWithin(a, b); }); });
    return groups.filter(function (g) { return g.items.length > 0; });
  }

  function fmtStars(n) {
    if (typeof n !== "number" || !isFinite(n) || n < 0) return "–";
    if (n < 1000) return String(Math.round(n));
    var k = n / 1000;
    var s = k < 10 ? (Math.round(k * 10) / 10).toFixed(1) : String(Math.round(k));
    return s.replace(/\.0$/, "") + "k";
  }

  function normName(s) { return str(s).replace(/\s+/g, " ").trim().toLowerCase(); }

  function pushUnique(map, key, it) {
    if (!key) return;
    if (!Object.prototype.hasOwnProperty.call(map, key)) map[key] = [];
    if (map[key].indexOf(it) < 0) map[key].push(it);
  }
  function buildPaperIndex(items) {
    var m = Object.create(null);
    arr(items).forEach(function (it) { if (it) arr(it.paper_ids).forEach(function (id) { pushUnique(m, str(id), it); }); });
    return m;
  }
  function buildDatasetIndex(items) {
    var m = Object.create(null);
    arr(items).forEach(function (it) { if (it) arr(it.dataset_names).forEach(function (n) { pushUnique(m, normName(n), it); }); });
    return m;
  }

  var api = {
    STAR_BUCKETS: STAR_BUCKETS, filterResources: filterResources, groupByKind: groupByKind, groupBySection: groupBySection, fmtStars: fmtStars,
    normName: normName, buildPaperIndex: buildPaperIndex, buildDatasetIndex: buildDatasetIndex
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else if (root) {
    root.AIVCResourcesData = api;
  }
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this));
