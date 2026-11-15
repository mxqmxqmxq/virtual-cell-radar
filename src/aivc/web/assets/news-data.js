/*
 * news-data.js — 商业新闻的纯函数（无界面、无依赖）。
 * 浏览器里挂 window.AIVCNewsData；Node 下 module.exports 同一对象。
 *
 *   filterNews(items, f)     f: {type, company(id 或名称), amount, q, major, includeUnscored, tier}
                            tier: "core"（默认，排除 tier=related）| "related"（只要相关）| "all"；缺 tier 视为 core
 *   isRelated(it)            it.tier === "related"
 *                            amount: ge100m | 10m_100m | lt10m | undisclosed
 *   groupByWeek(items)       -> [{id, start, end, items}]，周倒序；组内 relevance 降序，
 *                            同分区段按主公司轮转（公司自身 date 降序），最后 id
 *   interleaveByCompany(items, keyFn)
 *                            已排序列表中 keyFn 值相同的连续区段内按主公司轮转；区段间顺序不变
 *   fmtUsd(x)                -> "$1.2B" / "$120M" / "$850K" / "未披露"
 *   quarterBars(quarters)    -> 每项附 h（相对高度 0-1）
 *   weekRange(weekId)        -> {start, end}（ISO 周，周一至周日）或 null
 *   AMOUNT_BUCKETS           金额档位 [{id,label}]
 */
(function (root) {
  "use strict";

  var AMOUNT_BUCKETS = [
    { id: "ge100m", label: "≥ $100M" },
    { id: "10m_100m", label: "$10M – $100M" },
    { id: "lt10m", label: "< $10M" },
    { id: "undisclosed", label: "未披露" }
  ];

  function arr(x) { return Array.isArray(x) ? x : []; }
  function str(x) { return typeof x === "string" ? x : (x == null ? "" : String(x)); }
  function num(x) { return typeof x === "number" && isFinite(x) ? x : null; }

  function inBucket(amount, bucket) {
    var a = num(amount);
    if (bucket === "undisclosed") return a === null;
    if (a === null) return false;
    if (bucket === "ge100m") return a >= 1e8;
    if (bucket === "10m_100m") return a >= 1e7 && a < 1e8;
    if (bucket === "lt10m") return a < 1e7;
    return true;
  }

  function haystack(it) {
    return [it.title, it.summary_zh, it.significance_zh, arr(it.companies).join(" "),
      arr(it.investors).join(" "), it.source, arr(it.sources).map(function (s) { return s && s.name; }).join(" ")]
      .map(str).join("\n").toLowerCase();
  }

  function matchCompany(it, c) {
    var key = str(c).trim();
    if (!key) return true;
    if (arr(it.company_ids).indexOf(key) >= 0) return true;
    var low = key.toLowerCase();
    return arr(it.companies).some(function (n) { return str(n).toLowerCase() === low; });
  }

  function isRelated(it) { return !!it && it.tier === "related"; }

  function filterNews(items, f) {
    f = f || {};
    var tier = f.tier === "related" || f.tier === "all" ? f.tier : "core";
    var terms = str(f.q).trim().toLowerCase().split(/\s+/).filter(Boolean);
    return arr(items).filter(function (it) {
      if (!it || typeof it !== "object") return false;
      if (!f.includeUnscored && it.scored === false) return false;
      if (tier === "core" && isRelated(it)) return false;
      if (tier === "related" && !isRelated(it)) return false;
      if (f.type && it.event_type !== f.type) return false;
      if (f.company && !matchCompany(it, f.company)) return false;
      if (f.amount && !inBucket(it.amount_usd, f.amount)) return false;
      if (f.major && !it.major) return false;
      if (terms.length) {
        var h = haystack(it);
        for (var i = 0; i < terms.length; i++) if (h.indexOf(terms[i]) < 0) return false;
      }
      return true;
    });
  }

  function cmpItems(a, b) {
    var ra = num(a.relevance) || 0, rb = num(b.relevance) || 0;
    if (ra !== rb) return rb - ra;
    var da = str(a.date), db = str(b.date);
    if (da !== db) return da < db ? 1 : -1;
    return str(a.id) < str(b.id) ? -1 : str(a.id) > str(b.id) ? 1 : 0;
  }

  // 主公司：company_ids[0]，没有则 companies[0]，都没有返回 ""（视为独立）
  function primaryCompany(it) {
    var a = arr(it && it.company_ids), b = arr(it && it.companies);
    return str(a.length ? a[0] : (b.length ? b[0] : ""));
  }

  function interleaveByCompany(items, keyFn) {
    var list = arr(items), out = [], i = 0;
    var kf = typeof keyFn === "function" ? keyFn : function () { return ""; };
    while (i < list.length) {
      var k = kf(list[i]), j = i + 1;
      while (j < list.length && kf(list[j]) === k) j++;
      var buckets = {}, order = [];
      for (var x = i; x < j; x++) {
        var c = primaryCompany(list[x]);
        var bk = c ? "c:" + c : "u:" + x;
        if (!buckets[bk]) { buckets[bk] = []; order.push(bk); }
        buckets[bk].push(list[x]);
      }
      var queues = order.map(function (b) { return buckets[b]; });
      while (queues.length) {
        var next = [];
        for (var q = 0; q < queues.length; q++) {
          out.push(queues[q].shift());
          if (queues[q].length) next.push(queues[q]);
        }
        queues = next;
      }
      i = j;
    }
    return out;
  }

  function sortWeekItems(items) {
    var sorted = arr(items).slice().sort(cmpItems);
    return interleaveByCompany(sorted, function (it) { return num(it.relevance) || 0; });
  }

  function weekRange(weekId) {
    var m = /^(\d{4})-W(\d{2})$/.exec(str(weekId));
    if (!m) return null;
    var y = +m[1], w = +m[2];
    if (w < 1 || w > 53) return null;
    var jan4 = new Date(Date.UTC(y, 0, 4));
    var dow = jan4.getUTCDay() || 7;
    var mon = Date.UTC(y, 0, 4 - (dow - 1) + (w - 1) * 7);
    function iso(t) { return new Date(t).toISOString().slice(0, 10); }
    return { start: iso(mon), end: iso(mon + 6 * 86400000) };
  }

  function groupByWeek(items) {
    var map = {}, order = [];
    arr(items).forEach(function (it) {
      var w = str(it.week) || "unknown";
      if (!map[w]) { map[w] = []; order.push(w); }
      map[w].push(it);
    });
    order.sort(function (a, b) { return a < b ? 1 : a > b ? -1 : 0; });
    return order.map(function (w) {
      var r = weekRange(w) || { start: "", end: "" };
      return { id: w, start: r.start, end: r.end, items: sortWeekItems(map[w]) };
    });
  }

  function trim1(v) { return String(Math.round(v * 10) / 10); }
  function fmtUsd(x) {
    var a = num(x);
    if (a === null) return "未披露";
    if (a >= 1e9) return "$" + trim1(a / 1e9) + "B";
    if (a >= 1e6) return "$" + trim1(a / 1e6) + "M";
    if (a >= 1e3) return "$" + Math.round(a / 1e3) + "K";
    return "$" + Math.round(a);
  }

  function quarterBars(quarters) {
    var list = arr(quarters).filter(function (q) { return q && typeof q === "object"; });
    var max = 0;
    list.forEach(function (q) { max = Math.max(max, num(q.total_usd) || 0); });
    return list.map(function (q) {
      var t = num(q.total_usd) || 0;
      var h = max > 0 ? t / max : 0;
      var o = {};
      Object.keys(q).forEach(function (k) { o[k] = q[k]; });
      o.h = t > 0 ? Math.max(h, 0.03) : 0;
      return o;
    });
  }

  var api = {
    AMOUNT_BUCKETS: AMOUNT_BUCKETS, filterNews: filterNews, isRelated: isRelated, groupByWeek: groupByWeek, interleaveByCompany: interleaveByCompany,
    fmtUsd: fmtUsd, quarterBars: quarterBars, weekRange: weekRange
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else if (root) {
    root.AIVCNewsData = api;
  }
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this));
