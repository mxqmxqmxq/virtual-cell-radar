/*
 * papers-data.js — 论文流的纯函数（无界面、无依赖）。
 * 浏览器里挂 window.AIVCPapersData；Node 下 module.exports 同一对象。
 *
 *   SOURCE_LABELS               来源 id -> 友好名
 *   sourceLabel(id)             -> 友好名；未知 id 原样返回，空值返回 ""
 *   allSourcesOf(p)             -> [source, ...sources] 去重保序（主来源在前）
 *   pubInfo(p)                  -> {published, venue, doi, isPromoted}
 *   rubricDims(p)               -> [{key,label,short,value}] | null（仅 v4 已打分论文）
 *   collectSources(papers, meta)-> [[id,label],...] 按 id 升序
 *   collectOrgs(papers)         -> [{name,count}] 频次降序、同频名称升序
 *   paperHaystack(p)            -> 全文检索用小写串（按论文对象缓存）
 *   filterAndSortPapers(papers, f, opts)
 *        f: {q,circle,source,org,scored,must,newds,pub,code,weights,task,paradigm,topic,from,to,sort}
 *        opts.topicsOf(p) -> [{id,...}]
 *        sort: date(默认) | final | novelty | citations | hf | keyword
 */
(function (root) {
  "use strict";

  var SOURCE_LABELS = {
    arxiv: "arXiv", biorxiv: "bioRxiv", medrxiv: "medRxiv", openalex: "OpenAlex",
    europepmc: "Europe PMC", hf_papers: "HF Papers", openreview: "OpenReview",
    journal: "期刊", pubmed: "PubMed", chemrxiv: "ChemRxiv", seed_pdf: "本地 PDF", other: "其他"
  };
  var PREPRINT_SOURCES = ["arxiv", "biorxiv", "medrxiv", "chemrxiv", "hf_papers"];

  function arr(x) { return Array.isArray(x) ? x : []; }
  function str(x) { return typeof x === "string" ? x : (x == null ? "" : String(x)); }
  function num(x) { return typeof x === "number" && isFinite(x) ? x : null; }
  function isObj(p) { return !!p && typeof p === "object"; }

  function sourceLabel(id) {
    var s = str(id);
    return Object.prototype.hasOwnProperty.call(SOURCE_LABELS, s) ? SOURCE_LABELS[s] : s;
  }

  function allSourcesOf(p) {
    if (!isObj(p)) return [];
    var out = [];
    [p.source].concat(arr(p.sources)).forEach(function (s) {
      var v = str(s);
      if (v && out.indexOf(v) < 0) out.push(v);
    });
    return out;
  }

  function httpUrl(value) {
    if (typeof value !== "string" || !/^https?:\/\//i.test(value)) return null;
    try { return new URL(value).href; } catch (e) { return null; }
  }

  function doiUrl(value) {
    var id = str(value).trim();
    var absolute = httpUrl(id);
    if (absolute) return absolute;
    if (!/^10\.\d{4,9}\/\S+$/i.test(id) || /[\\\x00-\x1f\x7f]/.test(id)) return null;
    var parts = id.split("/");
    if (parts.some(function (part) { return part === "." || part === ".."; })) return null;
    try { return "https://doi.org/" + parts.map(encodeURIComponent).join("/"); }
    catch (e) { return null; }
  }

  function orgsOf(p) {
    return Array.from(new Set(arr(p.matched_orgs).filter(function (o) {
      return typeof o === "string";
    }).map(function (o) { return o.trim(); }).filter(Boolean)));
  }

  function pubInfo(p) {
    p = isObj(p) ? p : {};
    var venue = str(p.published_venue).trim();
    var published = !!venue;
    return {
      published: published,
      venue: venue,
      doi: str(p.published_doi) || str(p.doi),
      isPromoted: published && PREPRINT_SOURCES.indexOf(p.source) >= 0
    };
  }

  var DIMS = [
    ["relevance", "相关度", "相"], ["novelty", "新颖度", "新"],
    ["rigor", "严谨度", "严"], ["impact", "影响力", "影"]
  ];
  function rubricDims(p) {
    if (!isObj(p) || !p.scored) return null;
    var nov = num(p.novelty_score), rig = num(p.rigor_score), imp = num(p.impact_score);
    if (nov === null && rig === null && imp === null) return null;
    var rel = num(p.relevance);
    if (rel === null) rel = num(p.relevance_score);
    var vals = { relevance: rel, novelty: nov, rigor: rig, impact: imp };
    var out = [];
    DIMS.forEach(function (d) {
      if (vals[d[0]] !== null) out.push({ key: d[0], label: d[1], short: d[2], value: vals[d[0]] });
    });
    return out;
  }

  function collectSources(papers, meta) {
    var seen = Object.create(null), ids = [];
    function add(id) { if (id && !seen[id]) { seen[id] = true; ids.push(id); } }
    var bs = meta && meta.stats && meta.stats.by_source;
    Object.keys(bs && typeof bs === "object" ? bs : {}).forEach(add);
    arr(papers).forEach(function (p) { allSourcesOf(p).forEach(add); });
    ids.sort(function (a, b) { return a < b ? -1 : a > b ? 1 : 0; });
    return ids.map(function (id) { return [id, sourceLabel(id)]; });
  }

  function collectOrgs(papers) {
    var cnt = Object.create(null), names = [];
    arr(papers).forEach(function (p) {
      if (!isObj(p)) return;
      orgsOf(p).forEach(function (n) {
        if (!Object.prototype.hasOwnProperty.call(cnt, n)) { cnt[n] = 0; names.push(n); }
        cnt[n]++;
      });
    });
    return names.map(function (n) { return { name: n, count: cnt[n] }; }).sort(function (a, b) {
      if (a.count !== b.count) return b.count - a.count;
      return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
    });
  }

  var hayCache = new WeakMap();
  function paperHaystack(p) {
    if (!isObj(p)) return "";
    if (hayCache.has(p)) return hayCache.get(p);
    function j(x) { return Array.isArray(x) ? x.map(str).join(" ") : str(x); }
    var h = [p.title, p.abstract, j(p.authors), j(p.keywords), j(p.matched_keywords), j(p.datasets_used),
      j(p.new_dataset_names), p.venue, p.published_venue, j(p.matched_orgs), p.novelty_zh,
      p.why_it_matters_zh, p.summary_zh].map(j).join("\n").toLowerCase();
    hayCache.set(p, h);
    return h;
  }

  function cmpDate(a, b) {
    var ad = str(a.date), bd = str(b.date);
    if (ad !== bd) return ad < bd ? 1 : -1;
    var ai = str(a.id), bi = str(b.id);
    return ai < bi ? -1 : ai > bi ? 1 : 0;
  }
  function fs(p) { return p.scored ? num(p.final_score) : null; }
  function kw(p) { return num(p.keyword_score) || 0; }
  function fsv(p) { var v = fs(p); return v === null ? -Infinity : v; }

  // 以某数值指标为主序（pos: 仅正数算有值），并列 final_score 降序再日期。
  function byMetric(get, pos) {
    return function (a, b) {
      var av = get(a), bv = get(b);
      var ah = av !== null && (!pos || av > 0), bh = bv !== null && (!pos || bv > 0);
      if (ah !== bh) return ah ? -1 : 1;
      if (ah && av !== bv) return bv - av;
      var af = fsv(a), bf = fsv(b);
      if (af !== bf) return bf > af ? 1 : -1;
      return cmpDate(a, b);
    };
  }

  function comparator(sort) {
    if (sort === "final") {
      return function (a, b) {
        var af = fs(a), bf = fs(b);
        if ((af !== null) !== (bf !== null)) return af !== null ? -1 : 1;
        if (af !== null && af !== bf) return bf - af;
        return (kw(b) - kw(a)) || cmpDate(a, b);
      };
    }
    if (sort === "novelty") return byMetric(function (p) { return p.scored ? num(p.novelty_score) : null; }, false);
    if (sort === "citations") return byMetric(function (p) { return num(p.citation_count); }, true);
    if (sort === "hf") return byMetric(function (p) { return num(p.hf_upvotes); }, true);
    if (sort === "keyword") return function (a, b) { return (kw(b) - kw(a)) || cmpDate(a, b); };
    return cmpDate;
  }

  function filterAndSortPapers(papers, f, opts) {
    if (!Array.isArray(papers)) return [];
    f = isObj(f) ? f : {};
    opts = isObj(opts) ? opts : {};
    var topicsOf = typeof opts.topicsOf === "function" ? opts.topicsOf : function () { return []; };
    var q = str(f.q).trim().toLowerCase();
    var terms = q ? q.split(/\s+/) : [];
    var out = papers.filter(function (p) {
      if (!isObj(p)) return false;
      if (f.circle && p.circle !== f.circle) return false;
      if (f.source && allSourcesOf(p).indexOf(f.source) < 0) return false;
      if (f.org && orgsOf(p).indexOf(f.org) < 0) return false;
      if (f.scored === "1" && !p.scored) return false;
      if (f.scored === "0" && p.scored) return false;
      if (f.must === "1" && !p.must_read) return false;
      if (f.newds === "1" && !p.releases_new_dataset) return false;
      if (f.pub === "1" && !str(p.published_venue).trim()) return false;
      if (f.code === "1" && !httpUrl(p.code_url)) return false;
      if (f.weights === "1" && !httpUrl(p.weights_url)) return false;
      if (f.task && arr(p.tasks).indexOf(f.task) < 0) return false;
      if (f.paradigm && arr(p.paradigms).indexOf(f.paradigm) < 0) return false;
      if (f.topic) {
        var tm = arr(topicsOf(p));
        if (f.topic === "any") { if (!tm.length) return false; }
        else if (!tm.some(function (t) { return t && t.id === f.topic; })) return false;
      }
      if (f.from && str(p.date) < f.from) return false;
      if (f.to && str(p.date) > f.to) return false;
      if (terms.length) {
        var h = paperHaystack(p);
        for (var i = 0; i < terms.length; i++) if (h.indexOf(terms[i]) < 0) return false;
      }
      return true;
    });
    out.sort(comparator(f.sort || "date"));
    return out;
  }

  var api = {
    httpUrl: httpUrl, doiUrl: doiUrl, orgsOf: orgsOf,
    SOURCE_LABELS: SOURCE_LABELS, sourceLabel: sourceLabel, allSourcesOf: allSourcesOf, pubInfo: pubInfo,
    rubricDims: rubricDims, collectSources: collectSources, collectOrgs: collectOrgs,
    paperHaystack: paperHaystack, filterAndSortPapers: filterAndSortPapers
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else if (root) {
    root.AIVCPapersData = api;
  }
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this));
