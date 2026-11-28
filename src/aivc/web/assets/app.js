/* 虚拟细胞雷达前端：原生 JS 单页应用（hash 路由）。
 * 安全约定：论文数据一律经 textContent / el() 输出；唯一的 innerHTML 是 weekly.json 的 html（Python 侧已转义并消毒）。 */
(function () {
  "use strict";

  var root = document.documentElement;

  // ---------- 测试钩子 ----------
  function recordError(msg) {
    try {
      var prev = root.dataset.jsError;
      root.dataset.jsError = (prev ? prev + " | " : "") + String(msg).slice(0, 300);
    } catch (e) { /* ignore */ }
  }
  window.addEventListener("error", function (ev) {
    recordError((ev && ev.message) || "error");
  });
  window.addEventListener("unhandledrejection", function (ev) {
    var r = ev && ev.reason;
    recordError((r && r.message) || r || "unhandledrejection");
  });

  var reduceMotion = false;
  try { reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { /* ignore */ }
  if (!reduceMotion && "IntersectionObserver" in window) root.classList.add("motion");

  // ---------- 工具 ----------
  var D = { papers: [], meta: null, datasets: { new_datasets: [], used_top: [] }, weekly: { reports: [] }, home: null,
    news: { items: [], count: 0, event_types: [], weeks: [], funding_quarters: [] }, companies: { categories: {}, companies: [], paper_companies: {} },
    companyById: {}, paperCompanies: {},
    resources: { items: [], kinds: [], sections: [], tasks: [], paradigms: [], count: 0 }, resByPaper: {}, resByDataset: {} };
  var byId = Object.create(null);
  var app = document.getElementById("app");

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else if (k.slice(0, 2) === "on" && typeof v === "function") n.addEventListener(k.slice(2), v);
        else if (v === true) n.setAttribute(k, "");
        else n.setAttribute(k, String(v));
      });
    }
    appendKids(n, children);
    return n;
  }
  function appendKids(n, kids) {
    if (kids === null || kids === undefined || kids === false) return;
    if (!Array.isArray(kids)) kids = [kids];
    kids.forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      if (Array.isArray(c)) appendKids(n, c);
      else n.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
    });
  }
  function T(s) { return document.createTextNode(s == null ? "" : String(s)); }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function str(x) { return typeof x === "string" ? x : (x == null ? "" : String(x)); }
  function safeUrl(u) {
    var P = PD();
    if (P) return P.httpUrl(u);
    if (typeof u !== "string" || !/^https?:\/\//i.test(u)) return null;
    try { return new URL(u).href; } catch (e) { return null; }
  }
  function extLink(url, label, cls) {
    var u = safeUrl(url);
    if (!u) return null;
    var attrs = { href: u, target: "_blank", rel: "noopener noreferrer", "class": cls || "btn" };
    var m = /^(.*\S)\s*(↗)$/.exec(String(label));
    if (!m) { attrs.text = label; return el("a", attrs); }
    return el("a", attrs, [m[1] + " ", el("span", { "class": "arr", "aria-hidden": "true", text: m[2] })]);
  }
  function fmtScore(x) {
    if (typeof x !== "number" || !isFinite(x)) return "–";
    return Math.abs(x - Math.round(x)) < 0.05 ? String(Math.round(x)) : x.toFixed(1);
  }
  function labelOf(list, id) {
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i].label;
    return id;
  }
  function taskLabel(id) { return labelOf(arr(D.meta && D.meta.taxonomy && D.meta.taxonomy.tasks), id); }
  function parLabel(id) { return labelOf(arr(D.meta && D.meta.taxonomy && D.meta.taxonomy.paradigms), id); }
  function circleLabel(c) {
    var m = (D.meta && D.meta.circle_labels) || {};
    return m[c] || c || "未知";
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  var observer = null;
  function reveal(scope, skip) {
    var nodes = (scope || app).querySelectorAll(".reveal:not(.in)");
    if (!root.classList.contains("motion")) {
      nodes.forEach(function (n) { n.classList.add("in"); });
      return;
    }
    if (!observer) {
      observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { en.target.classList.add("in"); observer.unobserve(en.target); }
        });
      }, { threshold: 0.08, rootMargin: "0px 0px -4% 0px" });
    }
    var i = 0;
    nodes.forEach(function (n) {
      if (skip && skip(n)) { n.classList.add("reveal-skip", "in"); return; }
      n.style.setProperty("--d", Math.min(i++ % 6, 5) * 50 + "ms");
      observer.observe(n);
    });
  }

  // ---------- 动效工具包（供首页脚本复用） ----------
  var cleanups = [];
  function onCleanup(fn) { if (typeof fn === "function") cleanups.push(fn); }
  function runCleanups() {
    var list = cleanups; cleanups = [];
    list.forEach(function (fn) { try { fn(); } catch (e) { /* ignore */ } });
  }
  var reduced = !root.classList.contains("motion");
  function watch(node, onEnter, onLeave, opts) {
    opts = opts || {};
    if (!node) return function () {};
    if (!("IntersectionObserver" in window)) {
      if (typeof onEnter === "function") { try { onEnter(node); } catch (e) { recordError("watch: " + (e && e.message)); } }
      return function () {};
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        try {
          if (en.isIntersecting) {
            if (typeof onEnter === "function") onEnter(en.target);
            if (opts.once) io.disconnect();
          } else if (typeof onLeave === "function") onLeave(en.target);
        } catch (e) { recordError("watch: " + (e && e.message)); }
      });
    }, { threshold: typeof opts.threshold === "number" ? opts.threshold : 0.2 });
    io.observe(node);
    var stop = function () { io.disconnect(); };
    onCleanup(stop);
    return stop;
  }
  function countUp(node, to, opts) {
    opts = opts || {};
    var fmt = typeof opts.format === "function" ? opts.format : function (v) { return String(v); };
    if (!node) return;
    if (typeof to !== "number" || !isFinite(to)) { node.textContent = fmt(to); return; }
    to = Math.round(to);
    if (reduced || to <= 0) { node.textContent = fmt(to); return; }
    var dur = typeof opts.duration === "number" ? opts.duration : 1200;
    var t0 = null, raf = 0, dead = false;
    node.textContent = fmt(0);
    function tick(ts) {
      if (dead) return;
      if (t0 === null) t0 = ts;
      var k = Math.min(1, (ts - t0) / dur);
      var e = 1 - Math.pow(1 - k, 3);
      node.textContent = fmt(Math.round(to * e));
      if (k < 1) raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    onCleanup(function () { dead = true; cancelAnimationFrame(raf); });
  }
  function sequence(cfg) {
    cfg = cfg || {};
    var steps = arr(cfg.steps), timers = [], running = false, wantRun = false;
    function clearTimers() { timers.forEach(clearTimeout); timers = []; }
    function reset() { if (typeof cfg.reset === "function") { try { cfg.reset(); } catch (e) { recordError("sequence: " + (e && e.message)); } } }
    function runStep(fn) { try { fn(); } catch (e) { recordError("sequence: " + (e && e.message)); } }
    var self = {
      get running() { return running; },
      stop: function () { wantRun = false; running = false; clearTimers(); },
      finish: function () {
        self.stop(); reset();
        steps.forEach(function (s) { runStep(s[1]); });
      },
      start: function () {
        if (reduced) { self.finish(); return; }
        clearTimers(); wantRun = true; running = true;
        if (document.hidden) { running = false; return; }
        reset();
        var t = 0;
        steps.forEach(function (s) {
          t += s[0] || 0;
          timers.push(setTimeout(function () { runStep(s[1]); }, t));
        });
        if (typeof cfg.loopDelay === "number") {
          timers.push(setTimeout(function () { self.start(); }, t + cfg.loopDelay));
        } else {
          timers.push(setTimeout(function () { running = false; }, t));
        }
      }
    };
    function onVis() {
      if (document.hidden) { var w = wantRun; self.stop(); wantRun = w; }
      else if (wantRun && !running) self.start();
    }
    document.addEventListener("visibilitychange", onVis);
    onCleanup(function () { document.removeEventListener("visibilitychange", onVis); self.stop(); });
    return self;
  }

  // ---------- 通用小部件 ----------
  function tag(text, cls) { return el("span", { class: "tag" + (cls ? " " + cls : ""), text: text }); }

  // ---------- 论文数据模块（可选：papers-data.js 缺失时降级） ----------
  function PD() { try { return window.AIVCPapersData || null; } catch (e) { return null; } }
  function srcLabel(id) { var P = PD(); return P && typeof P.sourceLabel === "function" ? (P.sourceLabel(id) || "未知来源") : (str(id) || "未知来源"); }
  function sourcesOf(p) { var P = PD(); if (P && P.allSourcesOf) return P.allSourcesOf(p); return Array.from(new Set([p.source].concat(arr(p.sources)).filter(Boolean))); }
  function pubOf(p) {
    var P = PD(); if (P && P.pubInfo) return P.pubInfo(p);
    var v = str(p.published_venue).trim();
    return { published: !!v, venue: v, doi: str(p.published_doi) || str(p.doi), isPromoted: false };
  }
  function orgsOf(p) { return PD() ? PD().orgsOf(p) : arr(p.matched_orgs).filter(Boolean); }
  function rubricOf(p) { var P = PD(); return P && P.rubricDims ? P.rubricDims(p) : null; }
  function posNum(x) { return typeof x === "number" && isFinite(x) && x > 0 ? x : 0; }

  // ---------- 我的课题（可选模块：topics.js / topics-ui.js 缺失或出错时一律降级为“没有课题”） ----------
  function TP() { try { return window.AIVCTopics || null; } catch (e) { return null; } }
  function TUI() { try { return window.AIVCTopicsUI || null; } catch (e) { return null; } }
  function topicsAvailable() { var t = TP(), u = TUI(); return !!(t && u && typeof t.matchPaper === "function" && typeof u.open === "function"); }
  function topicsOf(p) {
    try { var t = TP(); if (t) return arr(t.matchPaper(p)); } catch (e) { /* ignore */ }
    return [];
  }
  function activeTopics() {
    try { var t = TP(); if (t) return arr(t.activeTopics()); } catch (e) { /* ignore */ }
    return [];
  }
  // 订阅课题变化；订阅随当前视图一起在下次路由渲染时取消
  function onTopicsChange(fn) {
    try {
      var t = TP();
      if (!t || typeof t.onChange !== "function") return;
      var unsub = t.onChange(function () { try { fn(); } catch (e) { /* ignore */ } });
      onCleanup(unsub);
    } catch (e) { /* ignore */ }
  }
  function setNavCurrent(name) {
    document.querySelectorAll("#nav a").forEach(function (a) {
      if (a.dataset.route === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  // 面板关闭：路由还停在 #/topics 时把地址改回 #/papers（replaceState 不触发 hashchange，也就不会重建论文流）
  function onPanelClosed() {
    try {
      var r = parseHash();
      if (r.name !== "topics") return;
      r.params.delete("import");
      var qs = r.params.toString();
      history.replaceState(null, "", "#/papers" + (qs ? "?" + qs : ""));
      setNavCurrent("papers");
      root.dataset.route = "papers";
      document.title = "论文流 · 虚拟细胞雷达";
    } catch (e) { /* ignore */ }
  }
  function openTopicsPanel(opener) {
    try {
      var u = TUI();
      if (!u || !TP()) return false;
      u.open({ papers: D.papers, opener: opener || null, onClose: onPanelClosed });
      return true;
    } catch (e) { return false; }
  }
  function updateTopicsNav() {
    var link = document.getElementById("nav-topics"), n = document.getElementById("nav-topics-n");
    if (!link) return;
    var ok = topicsAvailable();
    link.hidden = !ok;
    var c = ok ? activeTopics().length : 0;
    if (n) { n.hidden = !c; n.textContent = c ? String(c) : ""; }
    if (c) link.setAttribute("aria-label", "我的课题，已启用 " + c + " 个"); else link.removeAttribute("aria-label");
  }
  function initTopics(data) {
    try {
      var t = TP();
      if (t && typeof t.init === "function") {
        t.init({});
        t.setPresets(data && typeof data === "object" && data.version === 1 && Array.isArray(data.presets) ? data.presets : []);
        if (typeof t.onChange === "function") t.onChange(function () { try { updateTopicsNav(); } catch (e) { /* ignore */ } });
      }
    } catch (e) { /* 课题模块出错：视为没有课题功能 */ }
    updateTopicsNav();
    var link = document.getElementById("nav-topics");
    if (link) link.addEventListener("click", function (ev) {
      if (ev.defaultPrevented || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      var name = parseHash().name;
      // 论文流 / 周报页就地打开面板，保留滚动位置与筛选；其他页面走 #/topics 路由
      if (name === "papers" || name === "weekly") { if (openTopicsPanel(link)) ev.preventDefault(); }
    });
  }
  // #/topics?import=<载荷>：解析后弹确认框；无论结果如何都立刻把载荷从地址里去掉，绝不自动生效
  function handleImportHash() {
    var m = /[?&]import=([^&]*)/.exec(location.hash || "");
    if (!m) return false;
    var raw = m[1];
    try {
      var r = parseHash();
      r.params.delete("import");
      var qs = r.params.toString();
      history.replaceState(null, "", "#/papers" + (qs ? "?" + qs : ""));
      setNavCurrent("papers");
      root.dataset.route = "papers";
      document.title = "论文流 · 虚拟细胞雷达";
    } catch (e) { /* ignore */ }
    var ok = false;
    try {
      var t = TP(), u = TUI();
      if (t && u && typeof u.confirmImport === "function") {
        var dec = t.decodeShare(raw);
        if (dec && dec.ok && arr(dec.topics).length) { u.confirmImport(dec.topics, {}); ok = true; }
      }
    } catch (e) { ok = false; }
    if (!ok) toast("导入链接无效");
    return true;
  }
  function handleTopicsRoute() {
    if (handleImportHash()) return;
    if (!openTopicsPanel(document.getElementById("nav-topics"))) {
      try { history.replaceState(null, "", "#/papers"); setNavCurrent("papers"); root.dataset.route = "papers"; } catch (e) { /* ignore */ }
    }
  }

  function scoreBadge(p) {
    if (p.scored && Number.isFinite(p.final_score)) {
      return el("span", { class: "score" + (p.must_read ? " must" : ""), text: (p.must_read ? "必读 " : "") + fmtScore(p.final_score) });
    }
    if (p.scored) return el("span", { class: "score un", text: "暂无最终分" });
    return el("span", { class: "score un", text: "未打分 · 关键词分 " + fmtScore(p.keyword_score) });
  }

  function paperCard(p) {
    var tags = [];
    arr(p.tasks).slice(0, 3).forEach(function (t) { tags.push(tag(taskLabel(t), "tag-task")); });
    arr(p.paradigms).slice(0, 3).forEach(function (t) { tags.push(tag(parLabel(t), "tag-par")); });
    topicsOf(p).slice(0, 2).forEach(function (h) { tags.push(tag("课题 · " + str(h.name), "tag-hl")); });
    if (p.releases_new_dataset) tags.push(tag("新数据集", "tag-good"));
    var nov = str(p.novelty_zh);
    var srcs = sourcesOf(p), pub = pubOf(p);
    var metaKids = [el("span", { class: "pcard-src", text: srcLabel(p.source) })];
    if (srcs.length > 1) {
      var allNames = srcs.map(srcLabel).join(" · ");
      metaKids.push(el("span", { class: "pcard-multi", title: allNames, "aria-label": "共 " + srcs.length + " 个来源：" + allNames, text: "+" + (srcs.length - 1) }));
    }
    if (pub.published) {
      var pubText = "已发表" + (pub.venue ? " · " + pub.venue : "");
      metaKids.push(el("span", { class: "pcard-pub", title: pubText, text: pubText }));
    }
    metaKids.push(el("span", { text: str(p.date) }), el("span", { text: circleLabel(p.circle) }));
    var rubricEl = null;
    var dims = rubricOf(p);
    if (dims && dims.length) {
      var items = dims.filter(function (d) { return d.key === "novelty" || d.key === "rigor" || d.key === "impact"; });
      if (items.length) {
        var kids = [];
        items.forEach(function (d, i) {
          kids.push(el("span", { class: "pcard-rubric-i", text: d.short + " " + fmtScore(d.value) }));
          if (i < items.length - 1) kids.push(el("span", { class: "pcard-rubric-sep", "aria-hidden": "true", text: "·" }));
        });
        rubricEl = el("div", { class: "pcard-rubric mono", "aria-label": "评分维度：" + items.map(function (d) { return d.label + " " + fmtScore(d.value); }).join("，") + "（满分 10）" }, kids);
      }
    }
    var sigs = [];
    if (safeUrl(p.code_url)) sigs.push(el("span", { class: "sig", text: posNum(p.github_stars) ? "Code ★ " + p.github_stars : "Code" }));
    if (safeUrl(p.weights_url)) sigs.push(el("span", { class: "sig", text: "权重" }));
    if (posNum(p.citation_count)) sigs.push(el("span", { class: "sig", text: "引用 " + p.citation_count }));
    if (posNum(p.hf_upvotes)) sigs.push(el("span", { class: "sig", text: "HF ▲" + p.hf_upvotes }));
    var orgs = orgsOf(p);
    orgs.slice(0, 2).forEach(function (o) { sigs.push(el("span", { class: "sig sig-org", text: o })); });
    if (orgs.length > 2) sigs.push(el("span", { class: "sig sig-more", title: orgs.slice(2).join("、"), text: "+" + (orgs.length - 2) }));
    var node = el("button", { type: "button", class: "card pcard reveal", "aria-haspopup": "dialog" }, [
      el("div", { class: "pcard-meta" }, metaKids),
      el("div", { class: "pcard-title", text: str(p.title) || "(无标题)" }),
      nov ? el("p", { class: "pcard-nov", text: nov }) :
        (p.scored ? null : el("p", { class: "pcard-nov hint", text: p.abstract ? "尚未打分，暂无中文创新点。" : "尚未打分，且该条目没有摘要。" })),
      rubricEl,
      sigs.length ? el("div", { class: "pcard-signals" }, sigs) : null,
      el("div", { class: "pcard-foot" }, [el("div", {}, scoreBadge(p)), tags.length ? el("div", { class: "tags" }, tags) : null])
    ]);
    node.addEventListener("click", function () { openDrawer(p, node); });
    return node;
  }

  // ---------- 详情抽屉 ----------
  var dlg = null, lastOpener = null;
  var closeTimer = 0, closeListener = null;
  function endClosing() {
    clearTimeout(closeTimer); closeTimer = 0;
    if (closeListener && dlg) dlg.removeEventListener("animationend", closeListener);
    closeListener = null;
    if (dlg) dlg.classList.remove("closing");
  }
  // 关闭走同一函数：先播退场动画，animationend（带超时兜底）后再 close()；reduced 时立即关闭
  function closeDrawer() {
    var d = dlg;
    if (!d || !d.open) return;
    if (reduced) { d.close(); return; }
    if (d.classList.contains("closing")) return;
    d.classList.add("closing");
    var done = false;
    function fin() {
      if (done) return; done = true;
      endClosing();
      if (d.open) d.close();
    }
    function onEnd(ev) { if (ev.target === d) fin(); }
    closeListener = onEnd;
    d.addEventListener("animationend", onEnd);
    closeTimer = setTimeout(fin, 320);
  }
  function ensureDialog() {
    if (dlg) return dlg;
    dlg = el("dialog", { class: "drawer", "aria-label": "论文详情" });
    dlg.addEventListener("click", function (ev) { if (ev.target === dlg) closeDrawer(); });
    dlg.addEventListener("cancel", function (ev) { ev.preventDefault(); closeDrawer(); });
    dlg.addEventListener("close", function () {
      if (dlg.open) return;
      endClosing();
      if (lastOpener && document.contains(lastOpener)) { try { lastOpener.focus(); } catch (e) { /* ignore */ } }
    });
    document.body.appendChild(dlg);
    return dlg;
  }
  function section(title, body) { return body ? [el("h4", { text: title }), body] : null; }
  function relatedCompanies(p) {
    var chips = arr(D.paperCompanies[p.id]).map(function (id) { return D.companyById[id]; }).filter(Boolean).map(function (c) {
      return el("a", { class: "tag tag-task", href: "#/news/company/" + encodeURIComponent(c.id), text: str(c.name), "aria-label": "公司档案：" + str(c.name) });
    });
    return chips.length ? el("div", { class: "tags nw-draw-chips" }, chips) : null;
  }
  function resourceLinks(list) {
    if (!list.length) return null;
    return el("div", { class: "tags" }, list.map(function (r) {
      return el("a", { class: "tag tag-task", href: "#/resources?id=" + encodeURIComponent(str(r.id)), text: str(r.name), "aria-label": "知识资源：" + str(r.name) });
    }));
  }
  function datasetResLink(name) {
    var RD = window.AIVCResourcesData;
    var list = RD ? arr(D.resByDataset[RD.normName(name)]) : [];
    if (!list.length) return null;
    return el("a", { class: "rs-dsl", href: "#/resources?id=" + encodeURIComponent(str(list[0].id)), "aria-label": "知识资源：" + str(list[0].name),
      text: "资源 →" });
  }
  function openDrawer(p, opener) {
    var d = ensureDialog();
    endClosing();
    lastOpener = opener || null;
    clear(d);
    var doi = str(p.doi), pubDoi = str(p.published_doi);
    var pub = pubOf(p);
    var hasPubDoi = !!pubDoi && pubDoi !== doi;
    var pubUrl = PD() ? PD().doiUrl(pubDoi) : null;
    var codeLabel = posNum(p.github_stars) ? "代码 ★" + p.github_stars + " ↗" : "代码 ↗";
    var links = [extLink(p.url, "原文 ↗", "btn btn-primary"), extLink(p.pdf_url, "PDF ↗"),
      hasPubDoi ? extLink(pubUrl, "正式发表 ↗") : null,
      extLink(p.code_url, codeLabel), extLink(p.weights_url, "模型权重 ↗")].filter(Boolean);
    var doiSpans = [];
    if (hasPubDoi) {
      if (doi) doiSpans.push(el("span", { class: "kv", text: "预印本 DOI: " + doi }));
      doiSpans.push(el("span", { class: "kv", text: "发表 DOI: " + pubDoi }));
    } else if (doi) doiSpans.push(el("span", { class: "kv", text: "DOI: " + doi }));
    var dims = rubricOf(p);
    var rubricCard = dims && dims.length ? el("div", { class: "rubric-card", role: "group", "aria-label": "v4 四维评分" }, dims.map(function (dm) {
      var v = Math.max(0, Math.min(10, dm.value));
      var fill = el("div", { class: "rubric-fill" });
      fill.style.width = (v * 10) + "%";
      return el("div", { class: "rubric-row" }, [
        el("span", { class: "rubric-label", text: dm.label }),
        el("div", { class: "rubric-track", role: "meter", "aria-valuemin": "0", "aria-valuemax": "10", "aria-valuenow": String(v), "aria-label": dm.label + " " + fmtScore(dm.value) + " / 10" }, fill),
        el("span", { class: "rubric-val mono", text: fmtScore(dm.value) + " / 10" })
      ]);
    })) : null;
    var mItems = [];
    if (posNum(p.citation_count)) mItems.push({ l: "引用数", v: String(p.citation_count) });
    if (posNum(p.hf_upvotes)) mItems.push({ l: "HF 热度", v: "▲" + p.hf_upvotes });
    if (posNum(p.github_stars)) mItems.push({ l: "GitHub Stars", v: "★ " + p.github_stars });
    if (posNum(p.semantic_score)) mItems.push({ l: "语义分", v: p.semantic_score.toFixed(3) });
    var metricStrip = mItems.length ? el("div", { class: "metric-strip" }, mItems.map(function (i) {
      return el("div", { class: "metric" }, [el("span", { class: "metric-l", text: i.l }), el("span", { class: "metric-v mono", text: i.v })]);
    })) : null;
    var orgs = orgsOf(p);
    var tags = [];
    arr(p.tasks).forEach(function (t) { tags.push(tag(taskLabel(t), "tag-task")); });
    arr(p.paradigms).forEach(function (t) { tags.push(tag(parLabel(t), "tag-par")); });
    topicsOf(p).forEach(function (h) { tags.push(tag("课题 · " + str(h.name), "tag-hl")); });
    var inner = el("div", { class: "drawer-in" }, [
      el("button", { type: "button", class: "btn drawer-close", text: "关闭 Esc", onclick: closeDrawer }),
      el("div", { class: "pcard-meta" }, [
        el("span", { text: sourcesOf(p).map(srcLabel).join(" · ") || "未知来源" }),
        pub.isPromoted ? el("span", { class: "drawer-pub", text: "预印本 → 已发表：" + pub.venue })
          : (pub.published ? el("span", { class: "drawer-pub", text: "已发表：" + pub.venue }) : null),
        str(p.venue) && str(p.venue) !== pub.venue ? el("span", { text: str(p.venue) }) : null,
        el("span", { text: str(p.date) }),
        el("span", { text: circleLabel(p.circle) })
      ]),
      el("h2", { text: str(p.title) || "(无标题)" }),
      el("div", { class: "tags" }, [scoreBadge(p), p.releases_new_dataset ? tag("新数据集", "tag-good") : null]),
      rubricCard,
      metricStrip,
      p.scored ? null : el("div", { class: "notice", style: "margin-top:16px", text: "该论文尚未经 LLM 打分：没有最终分、任务/范式标签和中文总结。可运行 uv run python -m aivc score 打分。" }),
      tags.length ? el("div", { class: "tags", style: "margin-top:12px" }, tags) : null,
      section("为何重要", str(p.why_it_matters_zh) ? el("p", { class: "drawer-callout", text: p.why_it_matters_zh }) : null),
      section("创新点", str(p.novelty_zh) ? el("p", { text: p.novelty_zh }) : null),
      section("中文总结", str(p.summary_zh) ? el("p", { text: p.summary_zh }) : null),
      section("局限性", str(p.limitations_zh) ? el("p", { class: "drawer-caveat", text: p.limitations_zh }) : null),
      section("摘要", el("p", { text: str(p.abstract) || "（该条目没有摘要）" })),
      section("作者", arr(p.authors).length ? el("p", { text: arr(p.authors).join(", ") }) : null),
      section("机构 / 实验室", orgs.length ? el("div", { class: "tags" }, orgs.map(function (o) {
        return el("a", { class: "tag tag-org", href: "#/papers?org=" + encodeURIComponent(str(o)), "aria-label": "按机构筛选：" + str(o), text: str(o), onclick: function () { closeDrawer(); } });
      })) : null),
      section("相关公司", relatedCompanies(p)),
      section("使用的数据集", arr(p.datasets_used).length ? el("div", { class: "tags" }, arr(p.datasets_used).map(function (n) { return tag(n); })) : null),
      section("新发布的数据集", arr(p.new_dataset_names).length ? el("div", { class: "tags" }, arr(p.new_dataset_names).map(function (n) { return tag(n, "tag-good"); })) : null),
      section("关键词", arr(p.matched_keywords).length || arr(p.keywords).length ?
        el("div", { class: "tags" }, arr(p.matched_keywords).map(function (n) { return tag(n, "tag-task"); }).concat(arr(p.keywords).map(function (n) { return tag(n); }))) : null),
      section("链接", links.length || doi || pubDoi ? el("div", { class: "links" }, links.concat(doiSpans)) : null),
      section("本地文件", str(p.local_file) ? el("span", { class: "kv", text: p.local_file }) : null),
      section("相关资源", resourceLinks(arr(D.resByPaper[p.id]))),
      section("条目", el("span", { class: "kv", text: str(p.id) + " · 入库 " + str(p.first_seen) + " · " + str(p.week) }))
    ]);
    d.appendChild(inner);
    if (typeof d.showModal === "function") { if (!d.open) d.showModal(); }
    else d.setAttribute("open", "");
    d.scrollTop = 0;
  }

  // ---------- 视图：首页 ----------
  function kpi(num, label) {
    return el("div", { class: "kpi reveal" }, [el("div", { class: "kpi-num", text: String(num == null ? "–" : num) }), el("div", { class: "kpi-label", text: label })]);
  }
  function viewHome() {
    var m = D.meta, s = m.stats || {};
    var cw = m.current_week || {};
    var mustNow = D.papers.filter(function (p) { return p.must_read && p.week === cw.id; });
    var note = null, picks = mustNow;
    if (!mustNow.length) {
      note = "本周暂无必读。下面是近期入库中分数较高的论文" + (s.scored ? "（已打分者按最终分，其余按关键词分）。" : "（当前全部按关键词分，尚未打分）。");
      picks = D.papers.slice().sort(function (a, b) { return b.first_seen < a.first_seen ? -1 : b.first_seen > a.first_seen ? 1 : 0; }).slice(0, 60);
      picks.sort(function (a, b) { return rank(b) - rank(a); });
      picks = picks.slice(0, 6);
    }
    var lw = m.latest_weekly;
    var page = el("div", {}, [
      el("section", { class: "hero", "data-nav-theme": "dark" }, el("div", { class: "hero-in" }, [
        el("div", { class: "eyebrow mono", text: "AIVC · Virtual Cell Radar" }),
        el("h1", {}, ["把虚拟细胞领域的", el("span", { class: "hl", text: "论文脉络" }), "整理成可检索的地图"]),
        el("p", { class: "lede", text: "每日抓取 arXiv、bioRxiv、OpenReview 等来源，经 LLM 打分、归类到任务与范式，汇总为论文流、范式地图、数据集表与每周简报。" }),
        el("div", { class: "hero-cta" }, [
          el("a", { class: "btn btn-primary", href: "#/papers", text: "浏览论文流 →" }),
          el("a", { class: "btn", href: "#/map", text: "范式地图" }),
          el("a", { class: "btn", href: "#/weekly", text: "周报" })
        ]),
        el("div", { class: "kpis" }, [
          kpi(s.total, "论文总数"), kpi(s.this_week_new, "本周新增"),
          kpi(s.must_read, "必读"), kpi(s.new_datasets, "新数据集")
        ])
      ])),
      el("div", { class: "wrap page", style: "padding-top:8px" }, [
        el("section", { class: "section" }, [
          el("div", { class: "section-head" }, [
            el("div", {}, [el("div", { class: "eyebrow mono", text: "本周 " + str(cw.id) + " · " + str(cw.start) + " ~ " + str(cw.end) }), el("h2", { text: "本周必读" })]),
            el("a", { class: "btn", href: "#/papers?must=1", text: "全部必读 →" })
          ]),
          note ? el("div", { class: "notice", text: note }) : null,
          picks.length ? el("div", { class: "grid" }, picks.map(paperCard)) : el("div", { class: "empty", text: "暂无论文。" })
        ]),
        el("section", { class: "section" }, [
          el("div", { class: "card weekly-entry reveal" }, lw ? [
            el("div", {}, [el("div", { class: "eyebrow mono", text: "最新周报" }), el("h3", { text: str(lw.title) || str(lw.id) }), el("div", { class: "hint", text: str(lw.start) + " ~ " + str(lw.end) })]),
            el("a", { class: "btn btn-primary", href: "#/weekly/" + encodeURIComponent(lw.id), text: "阅读 →" })
          ] : [
            el("div", {}, [el("div", { class: "eyebrow mono", text: "最新周报" }), el("h3", { text: "还没有周报" }),
              el("div", { class: "hint" }, ["运行 ", el("code", { text: "uv run python -m aivc weekly" }), " 生成，再 ", el("code", { text: "build-site" }), " 刷新本站。"])])
          ])
        ])
      ])
    ]);
    return page;
  }
  function homeView() {
    var H = window.AIVCHome;
    if (H && typeof H.render === "function") {
      try {
        var n = H.render(api);
        if (n && typeof n === "object" && n.nodeType === 1) return n;
      } catch (e) { recordError("home: " + ((e && e.message) || e)); }
    }
    return viewHome();
  }

  // 「关于与常见问题」：从首页移出的旅程分步、命令行终端、FAQ 三个模块（原样复用，任一缺失则跳过）
  function viewAbout() {
    var rails = el("div", { class: "hm-rails" });
    [["AIVCSteps", "section"], ["AIVCTerm", "section"], ["AIVCTerm", "faq"]].forEach(function (m) {
      var mod = window[m[0]];
      if (!mod || typeof mod[m[1]] !== "function") return;
      try { var n = mod[m[1]](api); if (n && n.nodeType === 1) rails.appendChild(n); }
      catch (e) { recordError("about " + m[0] + ": " + ((e && e.message) || e)); }
    });
    var headBox = el("div", { class: "hm-rails hm-about-head" }, el("div", { class: "hm-head" }, el("div", { class: "hm-head-t" }, [
      el("div", { class: "hm-eyebrow mono", text: "About" }),
      el("h1", { class: "hm-h2", text: "关于与常见问题" }),
      el("p", { class: "hint", text: "一篇论文从抓取到进入周报的完整旅程、命令行用法，以及常见问题。" })
    ])));
    if (!rails.children.length) rails.appendChild(el("div", { class: "empty", text: "这部分内容暂时无法加载，请刷新页面重试。" }));
    return el("div", { class: "hm-home hm-about" }, [headBox, rails]);
  }

  // ---------- 导航状态：滚动阴影 + 深色区块换肤 ----------
  var topbar = document.getElementById("topbar");
  var navTick = false;
  // 换肤判定线：下滚时深色区块边界越过视口顶部约 36px 翻转，上滚约 51px 翻转（带约 15px 滞回）
  var NAV_LINE_DOWN = 36, NAV_LINE_UP = 51, lastY = 0, scrollingUp = false;
  function updateNav() {
    navTick = false;
    if (!topbar) return;
    var y = window.scrollY || root.scrollTop || 0;
    if (y !== lastY) { scrollingUp = y < lastY; lastY = y; }
    if (y > 8) topbar.setAttribute("data-scrolled", "1"); else topbar.removeAttribute("data-scrolled");
    var line = scrollingUp ? NAV_LINE_UP : NAV_LINE_DOWN, over = false;
    var zones = document.querySelectorAll("[data-nav-theme='dark']");
    for (var i = 0; i < zones.length; i++) {
      var r = zones[i].getBoundingClientRect();
      if (r.top <= line && r.bottom > line && r.height > 0) { over = true; break; }
    }
    if (over) topbar.setAttribute("data-over", "dark"); else topbar.removeAttribute("data-over");
  }
  function queueNav() {
    if (navTick) return;
    navTick = true;
    requestAnimationFrame(updateNav);
  }
  window.addEventListener("scroll", queueNav, { passive: true });
  window.addEventListener("resize", queueNav, { passive: true });

  function rank(p) {
    if (p.scored && Number.isFinite(p.final_score)) return 1000 + p.final_score;
    return Number.isFinite(p.keyword_score) ? p.keyword_score : 0;
  }

  // ---------- 视图：论文流 ----------
  var FKEYS = ["q", "circle", "source", "org", "scored", "must", "newds", "pub", "code", "weights", "task", "paradigm", "topic", "from", "to", "sort"];
  var BATCH = 30;
  function haystack(p) {
    return [p.title, p.abstract, arr(p.authors).join(" "), arr(p.keywords).join(" "), arr(p.matched_keywords).join(" "),
      arr(p.datasets_used).join(" "), arr(p.new_dataset_names).join(" "), orgsOf(p).join(" "), p.venue, p.published_venue, p.summary_zh, p.novelty_zh, p.why_it_matters_zh].map(str).join("\n").toLowerCase();
  }
  function filterPapers(f) {
    var q = (f.q || "").trim().toLowerCase();
    var terms = q ? q.split(/\s+/) : [];
    var out = D.papers.filter(function (p) {
      if (f.circle && p.circle !== f.circle) return false;
      if (f.org && orgsOf(p).indexOf(f.org) < 0) return false;
      if (f.pub === "1" && !pubOf(p).published) return false;
      if (f.code === "1" && !safeUrl(p.code_url)) return false;
      if (f.weights === "1" && !safeUrl(p.weights_url)) return false;
      if (f.source && sourcesOf(p).indexOf(f.source) < 0) return false;
      if (f.scored === "1" && !p.scored) return false;
      if (f.scored === "0" && p.scored) return false;
      if (f.must === "1" && !p.must_read) return false;
      if (f.newds === "1" && !p.releases_new_dataset) return false;
      if (f.task && arr(p.tasks).indexOf(f.task) < 0) return false;
      if (f.paradigm && arr(p.paradigms).indexOf(f.paradigm) < 0) return false;
      if (f.topic) {
        var tm = topicsOf(p);
        if (f.topic === "any") { if (!tm.length) return false; }
        else if (!tm.some(function (t) { return t.id === f.topic; })) return false;
      }
      if (f.from && str(p.date) < f.from) return false;
      if (f.to && str(p.date) > f.to) return false;
      if (terms.length) {
        var h = haystack(p);
        for (var i = 0; i < terms.length; i++) if (h.indexOf(terms[i]) < 0) return false;
      }
      return true;
    });
    var sort = f.sort || "date";
    out.sort(function (a, b) {
      if (sort === "final") {
        var as = a.scored && Number.isFinite(a.final_score), bs = b.scored && Number.isFinite(b.final_score);
        if (as !== bs) return as ? -1 : 1;
        if (as && bs && a.final_score !== b.final_score) return b.final_score - a.final_score;
        return (b.keyword_score || 0) - (a.keyword_score || 0) || cmpDate(a, b);
      }
      if (sort === "keyword") return (b.keyword_score || 0) - (a.keyword_score || 0) || cmpDate(a, b);
      return cmpDate(a, b);
    });
    return out;
  }
  function queryPapers(f) {
    var P = PD();
    if (P && typeof P.filterAndSortPapers === "function") {
      try { return P.filterAndSortPapers(D.papers, f, { topicsOf: topicsOf }); }
      catch (e) { recordError("papers-data: " + ((e && e.message) || e)); }
    }
    return filterPapers(f);
  }
  function cmpDate(a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : 0; }

  function selectField(label, key, options, state, onChange) {
    var sel = el("select", { id: "f-" + key }, [el("option", { value: "", text: "全部" })].concat(options.map(function (o) {
      return el("option", { value: o[0], text: o[1] });
    })));
    sel.value = state[key] || "";
    sel.addEventListener("change", function () { onChange(key, sel.value); });
    return { node: el("div", { class: "field" }, [el("label", { for: "f-" + key, text: label }), sel]), input: sel };
  }

  function viewPapers(params) {
    var m = D.meta;
    var state = {};
    FKEYS.forEach(function (k) { var v = params.get(k); if (v) state[k] = v; });
    var shown = BATCH;
    var inputs = {};
    if (!topicsAvailable()) delete state.topic;
    if (!PD() && ["novelty", "citations", "hf"].indexOf(state.sort) >= 0) {
      delete state.sort;
      syncHash();
    }

    var results = el("div", { class: "results" });
    var countEl = el("span", { class: "count" });
    var clearBtn = el("button", { type: "button", class: "btn btn-ghost", text: "清除筛选" });

    function syncHash() {
      var sp = new URLSearchParams();
      FKEYS.forEach(function (k) { if (state[k] && !(k === "sort" && state[k] === "date")) sp.set(k, state[k]); });
      var qs = sp.toString();
      try { history.replaceState(null, "", "#/papers" + (qs ? "?" + qs : "")); } catch (e) { /* ignore */ }
    }
    function topicHint() {
      if (!state.topic) return null;
      var act = activeTopics();
      if (state.topic === "any") return act.length ? null : "还没有启用的课题，「任一课题」没有可匹配的论文。";
      var ok = act.some(function (t) { return t.id === state.topic; });
      return ok ? null : "课题「" + state.topic + "」不存在或未启用，所以没有匹配的论文。可在「我的课题」里检查，或清除筛选。";
    }
    function renderResults(prevShown) {
      var list = queryPapers(state);
      clear(results);
      var nUn = list.filter(function (p) { return !p.scored; }).length;
      var nPub = list.filter(function (p) { return pubOf(p).published; }).length;
      countEl.textContent = "共 " + list.length + " 篇" + (list.length ? " · 其中 " + nUn + " 篇未打分" : "") + (nPub ? " · " + nPub + " 篇已发表" : "");
      var any = FKEYS.some(function (k) { return state[k] && k !== "sort"; });
      clearBtn.style.display = any ? "" : "none";
      if (!list.length) {
        var th = topicHint();
        results.appendChild(el("div", { class: "empty" }, [
          "没有符合条件的论文。",
          th ? el("p", { class: "hint", style: "margin-top:8px", text: th }) : null,
          (state.task || state.paradigm || state.scored === "1" || state.must === "1" || state.newds === "1") && m.stats.scored < m.stats.total ?
            el("p", { class: "hint", style: "margin-top:8px", text: "任务、范式、必读等条件只对已打分的论文生效（目前已打分 " + m.stats.scored + " / " + m.stats.total + " 篇）。" }) : null
        ]));
        return;
      }
      var cards = list.slice(0, shown).map(paperCard);
      results.appendChild(el("div", { class: "grid" }, cards));
      if (list.length > shown) {
        results.appendChild(el("div", { class: "more" }, el("button", {
          type: "button", class: "btn", text: "加载更多（还有 " + (list.length - shown) + " 篇）",
          onclick: function () { var prev = shown; shown += BATCH; renderResults(prev); }
        })));
      }
      // 加载更多时，已显示过的卡片直接就位，只有新增的逐项出现
      reveal(results, prevShown ? function (n) { return cards.indexOf(n) > -1 && cards.indexOf(n) < prevShown; } : null);
    }
    function change(k, v) {
      if (v) state[k] = v; else delete state[k];
      shown = BATCH; syncHash(); renderResults();
    }

    var timer = null;
    onCleanup(function () { clearTimeout(timer); });
    var search = el("input", { type: "search", class: "search", id: "f-q", placeholder: "搜索标题 / 摘要 / 机构 / 期刊 / 作者 / 关键词…", "aria-label": "搜索论文", autocomplete: "off" });
    search.value = state.q || "";
    search.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { change("q", search.value.trim()); }, 220); });
    inputs.q = search;

    var PDm = PD();
    var sources = PDm && typeof PDm.collectSources === "function" ? PDm.collectSources(D.papers, m) :
      Object.keys((m.stats && m.stats.by_source) || {}).sort().map(function (s) { return [s, s]; });
    var orgList = PDm && typeof PDm.collectOrgs === "function" ? PDm.collectOrgs(D.papers) : [];
    var circles = Object.keys(m.circle_labels || {}).map(function (c) { return [c, m.circle_labels[c]]; });
    var fCircle = selectField("圈层", "circle", circles, state, change);
    var fSource = selectField("来源", "source", sources, state, change);
    var fOrg = selectField("机构", "org", orgList.map(function (o) { return [o.name, o.name + " (" + o.count + ")"]; }), state, change);
    if (!orgList.length && !state.org) fOrg.node.hidden = true;
    var fTask = selectField("任务", "task", arr(m.taxonomy && m.taxonomy.tasks).map(function (t) { return [t.id, t.label]; }), state, change);
    var fPar = selectField("范式", "paradigm", arr(m.taxonomy && m.taxonomy.paradigms).map(function (t) { return [t.id, t.label]; }), state, change);
    var fSort = selectField("排序", "sort", [["date", "日期（新→旧）"], ["final", "最终分（未打分在后）"], ["keyword", "关键词分"]].concat(PDm ? [["novelty", "新颖度（v4）"], ["citations", "引用数"], ["hf", "HF 热度"]] : []), state, change);
    fSort.input.firstChild.textContent = "默认";
    // 若 URL 里带了不在选项里的值（如手写的 task=x），补一个选项以保持筛选可见
    [["circle", fCircle], ["source", fSource], ["org", fOrg], ["task", fTask], ["paradigm", fPar]].forEach(function (pair) {
      var v = state[pair[0]];
      if (v && pair[1].input.value !== v) { pair[1].input.appendChild(el("option", { value: v, text: v })); pair[1].input.value = v; }
    });
    // 课题筛选：有启用课题时是下拉，没有时换成「设置我的课题」按钮；课题变化时就地重建
    var topicBox = el("div", { class: "field" });
    function buildTopicField() {
      clear(topicBox);
      delete inputs.topic;
      topicBox.hidden = !topicsAvailable();
      if (topicBox.hidden) return;
      var act = activeTopics();
      topicBox.appendChild(el("label", { "for": "f-topic", text: "课题" }));
      if (!act.length) {
        var btn = el("button", { type: "button", class: "btn f-topic-btn", id: "f-topic", text: "设置我的课题" });
        btn.addEventListener("click", function () { openTopicsPanel(btn); });
        topicBox.appendChild(btn);
        return;
      }
      var opts = [el("option", { value: "", text: "全部" }), el("option", { value: "any", text: "任一课题" })];
      act.forEach(function (t) { opts.push(el("option", { value: str(t.id), text: str(t.name) })); });
      var cur = state.topic || "";
      if (cur && cur !== "any" && !act.some(function (t) { return t.id === cur; })) opts.push(el("option", { value: cur, text: cur + "（不存在或未启用）" }));
      var sel = el("select", { id: "f-topic" }, opts);
      sel.value = cur;
      sel.addEventListener("change", function () { change("topic", sel.value); });
      topicBox.appendChild(sel);
      inputs.topic = sel;
    }
    buildTopicField();
    onTopicsChange(function () {
      var y = window.scrollY;
      buildTopicField();
      renderResults(shown); // 已显示的卡片就位，不重播入场动画
      if (window.scrollY !== y) window.scrollTo(0, y);
    });
    inputs.circle = fCircle.input; inputs.source = fSource.input; inputs.org = fOrg.input; inputs.task = fTask.input; inputs.paradigm = fPar.input; inputs.sort = fSort.input;

    function dateField(label, key) {
      var i = el("input", { type: "date", id: "f-" + key, min: "2000-01-01", max: "2100-12-31" });
      i.value = /^\d{4}-\d{2}-\d{2}$/.test(state[key] || "") ? state[key] : "";
      i.addEventListener("change", function () { change(key, i.value); });
      inputs[key] = i;
      return el("div", { class: "field" }, [el("label", { for: "f-" + key, text: label }), i]);
    }
    function check(label, key, val) {
      var c = el("input", { type: "checkbox" });
      c.checked = state[key] === val;
      c.addEventListener("change", function () { change(key, c.checked ? val : ""); });
      inputs[key + val] = c;
      return el("label", { class: "chk" }, [c, label]);
    }
    var scoredSel = el("select", { id: "f-scored" }, [el("option", { value: "", text: "全部" }), el("option", { value: "1", text: "仅已打分" }), el("option", { value: "0", text: "仅未打分" })]);
    scoredSel.value = state.scored || "";
    scoredSel.addEventListener("change", function () { change("scored", scoredSel.value); });
    inputs.scored = scoredSel;

    clearBtn.addEventListener("click", function () {
      clearTimeout(timer);
      FKEYS.forEach(function (k) { if (k !== "sort") delete state[k]; });
      search.value = "";
      Object.keys(inputs).forEach(function (k) {
        var i = inputs[k];
        if (k === "sort") return;
        if (i.type === "checkbox") i.checked = false; else if (i !== search && i.tagName !== "BUTTON") i.value = "";
      });
      shown = BATCH; syncHash(); renderResults();
    });

    var page = el("div", { class: "wrap page" }, [
      el("div", { class: "eyebrow mono", text: "Papers" }),
      el("h1", { class: "page-title", text: "论文流" }),
      el("p", { class: "lede", text: "全部入库论文。任务、范式、最终分来自 LLM 打分，未打分的论文只有关键词分。分数表示与 AIVC 研究范围的相关度，由程序自动标注，不代表对论文质量的评价。" }),
      m.stats.unscored ? el("div", { class: "notice", style: "margin-top:16px", text: "目前 " + m.stats.unscored + " / " + m.stats.total + " 篇尚未打分（运行 uv run python -m aivc score），相关筛选只覆盖已打分论文。" }) : null,
      el("div", { class: "card filters" }, [
        search,
        el("div", { class: "fgrid" }, [
          fCircle.node, fSource.node, fOrg.node, fTask.node, fPar.node, topicBox,
          el("div", { class: "field" }, [el("label", { for: "f-scored", text: "打分状态" }), scoredSel]),
          dateField("起始日期", "from"), dateField("截止日期", "to"), fSort.node
        ]),
        el("div", { class: "checks" }, [check("仅必读", "must", "1"), check("发布新数据集", "newds", "1"), check("已正式发表", "pub", "1"), check("有代码", "code", "1"), check("有权重", "weights", "1")]),
        el("div", { class: "fbar" }, [countEl, clearBtn])
      ]),
      results
    ]);
    renderResults();
    return page;
  }

  // ---------- 视图：范式地图 ----------
  function viewMap() {
    var m = D.meta;
    var tasks = arr(m.taxonomy.tasks), pars = arr(m.taxonomy.paradigms);
    var scored = D.papers.filter(function (p) { return p.scored; });
    var month = "";
    var holder = el("div");

    var monthSel = el("select", { id: "f-month" }, [el("option", { value: "", text: "全部月份" })].concat(arr(m.months).slice().reverse().map(function (x) {
      return el("option", { value: x, text: x });
    })));
    monthSel.addEventListener("change", function () { month = monthSel.value; render(); });

    function render() {
      clear(holder);
      var subset = month ? scored.filter(function (p) { return p.month === month; }) : scored;
      var counts = {}, max = 0, total = 0;
      subset.forEach(function (p) {
        arr(p.paradigms).forEach(function (pa) {
          arr(p.tasks).forEach(function (ta) {
            var k = pa + "|" + ta; counts[k] = (counts[k] || 0) + 1; total++;
            if (counts[k] > max) max = counts[k];
          });
        });
      });
      holder.appendChild(el("p", { class: "hint", text: "基于已打分的 " + subset.length + " 篇" + (month ? "（" + month + "）" : "") + "，共 " + D.papers.length + " 篇入库。一篇论文可同时属于多个任务和范式，因此格子之和可大于篇数。" }));
      if (!max) {
        holder.appendChild(el("div", { class: "empty", style: "margin:12px 0" }, [
          subset.length ? "这批已打分论文没有任务/范式标签。" : (scored.length ? "所选月份没有已打分论文。" : "还没有已打分的论文，矩阵为空。"),
          el("p", { class: "hint", style: "margin-top:8px" }, ["运行 ", el("code", { text: "uv run python -m aivc score" }), " 打分后再 ", el("code", { text: "build-site" }), "，这里就会出现热力图。"])
        ]));
      }
      var head = el("tr", {}, [el("th", { scope: "col", text: "" })].concat(tasks.map(function (t) { return el("th", { scope: "col", text: t.label }); })));
      var body = pars.map(function (pa, pi) {
        return el("tr", {}, [el("th", { scope: "row", text: pa.label })].concat(tasks.map(function (ta, ti) {
          var n = counts[pa.id + "|" + ta.id] || 0;
          var label = pa.label + " × " + ta.label + "：" + n + " 篇";
          if (!n) return el("td", {}, el("span", { class: "cell", "aria-label": label, text: "·" }));
          var a = 0.18 + 0.82 * (n / max);
          var b = el("button", { type: "button", class: "cell on mo-pop" + (a < 0.5 ? " lo" : ""), title: label, "aria-label": label + "，点击查看", text: String(n) });
          b.style.background = "rgba(var(--heat-rgb), " + a.toFixed(2) + ")";
          // 对角线错峰点亮：延迟 0..480ms，加 .42s 时长，总计不超过约 900ms
          b.style.setProperty("--mo-delay", Math.round(480 * (pi + ti) / Math.max(1, pars.length + tasks.length - 2)) + "ms");
          b.addEventListener("click", function () { location.hash = "#/papers?paradigm=" + encodeURIComponent(pa.id) + "&task=" + encodeURIComponent(ta.id); });
          return el("td", {}, b);
        })));
      });
      var heatCard = el("div", { class: "card heat-wrap reveal", "data-mo-demo": "" }, el("table", { class: "heat" }, [el("thead", {}, head), el("tbody", {}, body)]));
      holder.appendChild(heatCard);
      // 首次进入视口时点亮；切换月份会重建该卡片，因此自然重播
      if (!reduced) watch(heatCard, function () { api.motion.play(heatCard); }, null, { once: true, threshold: 0.15 });

      // 趋势：按月、每个范式的已打分论文数
      var months = arr(m.months);
      var tc = {}, tmax = 0;
      scored.forEach(function (p) {
        arr(p.paradigms).forEach(function (pa) {
          var k = pa + "|" + p.month; tc[k] = (tc[k] || 0) + 1; if (tc[k] > tmax) tmax = tc[k];
        });
      });
      var trend = el("div", { class: "trend" });
      if (tmax) {
        pars.forEach(function (pa) {
          var row = months.map(function (mo) {
            var n = tc[pa.id + "|" + mo] || 0;
            var b = el("div", { class: "tbar" + (n ? "" : " z"), title: pa.label + " · " + mo + "：" + n + " 篇" });
            if (n) b.style.height = Math.max(8, Math.round(100 * n / tmax)) + "%";
            return b;
          });
          trend.appendChild(el("div", { class: "trow" }, [el("span", { text: pa.label }), el("div", { class: "tbars" }, row)]));
        });
        trend.appendChild(el("div", { class: "tmonths" }, months.map(function (mo) { return el("span", { text: mo.slice(2), title: mo }); })));
      } else {
        trend.appendChild(el("div", { class: "empty", text: "有已打分论文后，这里会显示各范式按月的篇数趋势。" }));
      }
      holder.appendChild(el("section", { class: "section" }, [
        el("h2", { style: "font-size:24px;margin-bottom:6px", text: "按月趋势" }),
        el("p", { class: "hint", text: "每行一个范式，横轴为月份（YY-MM），柱高为该月已打分论文数（全局同一比例尺）。" }),
        el("div", { class: "card reveal" }, trend)
      ]));
      reveal(holder);
    }

    var page = el("div", { class: "wrap page" }, [
      el("div", { class: "eyebrow mono", text: "Paradigm × Task" }),
      el("h1", { class: "page-title", text: "范式地图" }),
      el("p", { class: "lede", text: "范式（行）与任务（列）的论文计数。点击格子，跳到论文流并带上对应筛选。" }),
      el("div", { class: "maptools" }, el("div", { class: "field", style: "min-width:180px" }, [el("label", { for: "f-month", text: "月份" }), monthSel])),
      holder
    ]);
    render();
    return page;
  }

  // ---------- 视图：数据集 ----------
  function viewDatasets() {
    var ds = D.datasets || {};
    var nw = arr(ds.new_datasets), used = arr(ds.used_top);
    function linksCell(r) {
      var a = [extLink(r.url, "原文", "tag"), extLink(r.pdf_url, "PDF", "tag")].filter(Boolean);
      return el("div", { class: "tags" }, a.length ? a : [el("span", { class: "hint", text: "–" })]);
    }
    var newTable = nw.length ? el("div", { class: "card tablewrap reveal" }, el("table", { class: "data" }, [
      el("thead", {}, el("tr", {}, ["数据集", "发布论文", "日期", "分数", "链接"].map(function (h) { return el("th", { text: h }); }))),
      el("tbody", {}, nw.map(function (r) {
        var p = byId[r.paper_id];
        return el("tr", {}, [
          el("td", {}, r.name ? [el("b", { text: r.name }), datasetResLink(r.name)] : el("span", { class: "hint", text: "（未命名）" })),
          el("td", {}, p ? el("button", { type: "button", class: "linkbtn", text: str(r.paper_title), onclick: function (ev) { openDrawer(p, ev.currentTarget); } }) : T(r.paper_title)),
          el("td", { class: "mono", text: str(r.date) }),
          el("td", { class: "mono", text: fmtScore(r.final_score) }),
          el("td", {}, linksCell(r))
        ]);
      }))
    ])) : el("div", { class: "empty" }, ["暂无新发布的数据集。这一列表来自已打分论文的 releases_new_dataset；目前已打分 " + D.meta.stats.scored + " / " + D.meta.stats.total + " 篇。"]);
    var usedTable = used.length ? el("div", { class: "card tablewrap reveal" }, el("table", { class: "data" }, [
      el("thead", {}, el("tr", {}, ["数据集", "论文数"].map(function (h) { return el("th", { text: h }); }))),
      el("tbody", {}, used.map(function (r) {
        return el("tr", {}, [
          el("td", {}, [el("a", { class: "linkbtn", href: "#/papers?q=" + encodeURIComponent(str(r.name)), text: str(r.name) }), datasetResLink(r.name)]),
          el("td", { class: "mono", text: String(r.count) })
        ]);
      }))
    ])) : el("div", { class: "empty" }, ["暂无数据集使用统计（需要先对论文打分）。"]);
    return el("div", { class: "wrap page" }, [
      el("div", { class: "eyebrow mono", text: "Datasets & Benchmarks" }),
      el("h1", { class: "page-title", text: "数据集与基准" }),
      el("p", { class: "lede", text: "汇总自已打分论文：新发布的数据集 / 基准，以及被使用最多的数据集。" }),
      el("section", { class: "section" }, [el("h2", { style: "font-size:24px;margin-bottom:14px", text: "新发布的数据集 / 基准" }), newTable]),
      el("section", { class: "section" }, [el("h2", { style: "font-size:24px;margin-bottom:6px", text: "使用最多的数据集" }), el("p", { class: "hint", text: "点击名称，在论文流中搜索该数据集。" }), usedTable])
    ]);
  }

  // ---------- 视图：周报 ----------
  function weeklyTopicsSection(cur) {
    if (!topicsAvailable()) return null;
    var box = el("section", { class: "card wk-topics", "aria-labelledby": "wk-topics-h" });
    function fill() {
      clear(box);
      box.appendChild(el("h2", { class: "wk-topics-h", id: "wk-topics-h", text: "本周与我的课题相关" }));
      var act = activeTopics();
      if (!act.length) {
        var btn = el("button", { type: "button", class: "btn", text: "设置我的课题" });
        btn.addEventListener("click", function () { openTopicsPanel(btn); });
        box.appendChild(el("p", { class: "hint", text: "还没有启用的课题。设置后，这里会列出本周入库且命中课题的论文。" }));
        box.appendChild(btn);
        return;
      }
      var weekPapers = D.papers.filter(function (p) { return p.week === cur.id; });
      var any = false;
      act.forEach(function (t) {
        var hits = weekPapers.filter(function (p) { return topicsOf(p).some(function (x) { return x.id === t.id; }); });
        if (!hits.length) return;
        any = true;
        hits.sort(function (a, b) {
          var as = a.scored && Number.isFinite(a.final_score) ? a.final_score : -1, bs = b.scored && Number.isFinite(b.final_score) ? b.final_score : -1;
          return bs - as || cmpDate(a, b);
        });
        box.appendChild(el("h3", { class: "wk-tg-h" }, [str(t.name), el("span", { class: "wk-tg-n", text: hits.length + " 篇" })]));
        box.appendChild(el("ul", { class: "wk-tg" }, hits.map(function (p) {
          var b = el("button", { type: "button", class: "wk-tp", "aria-haspopup": "dialog" }, [
            el("span", { class: "wk-tp-title", text: str(p.title) || "(无标题)" }),
            el("span", { class: "score" + (p.scored ? (p.must_read ? " must" : "") : " un"), text: p.scored && Number.isFinite(p.final_score) ? fmtScore(p.final_score) : "未打分" })
          ]);
          b.addEventListener("click", function () { openDrawer(p, b); });
          return el("li", {}, b);
        })));
      });
      if (!any) box.appendChild(el("p", { class: "hint", text: "本周没有命中。" }));
    }
    fill();
    onTopicsChange(fill);
    return box;
  }

  function viewWeekly(id) {
    var reps = arr(D.weekly.reports);
    var page = el("div", { class: "wrap page" }, [
      el("div", { class: "eyebrow mono", text: "Weekly Digest" }),
      el("h1", { class: "page-title", text: "周报" }),
      el("p", { class: "lede", text: "「本周」按论文入库时间（first_seen）划分，周一到周日。" })
    ]);
    if (!reps.length) {
      page.appendChild(el("div", { class: "empty", style: "margin-top:28px" }, [
        "还没有周报。运行 ", el("code", { text: "uv run python -m aivc weekly" }), " 生成，再运行 ", el("code", { text: "uv run python -m aivc build-site" }), " 刷新本站。"
      ]));
      return page;
    }
    var cur = null;
    for (var i = 0; i < reps.length; i++) if (reps[i].id === id) cur = reps[i];
    var notFound = id && !cur;
    if (!cur) cur = reps[0];
    var list = el("nav", { class: "wk-list", "aria-label": "周报列表" }, reps.map(function (r) {
      return el("a", { class: "wk-item", href: "#/weekly/" + encodeURIComponent(r.id), "aria-current": r.id === cur.id ? "page" : null }, [
        el("b", { text: str(r.id) }), el("span", { text: str(r.start) + " ~ " + str(r.end) })
      ]);
    }));
    var body = el("article", { class: "card md reveal" });
    body.innerHTML = str(cur.html); // 唯一允许的 innerHTML：Python 侧 mdhtml 已转义并只放行 http(s) 链接
    page.appendChild(el("div", { class: "wk" }, [list, el("div", {}, [
      notFound ? el("div", { class: "notice", text: "没有找到周报 " + id + "，已显示最新一期。" }) : null, body,
      weeklyTopicsSection(cur)
    ])]));
    return page;
  }

  // ---------- 视图：占位 ----------
  function viewSoon(eyebrow, title, phase, desc) {
    return el("div", { class: "wrap page" }, el("div", { class: "card soon reveal" }, [
      el("div", { class: "eyebrow mono", text: eyebrow }),
      el("h2", {}, [title + " ", el("span", { class: "hl", text: "即将上线" })]),
      el("p", { class: "lede", style: "margin:0 auto", text: desc }),
      el("p", { class: "mono hint", style: "margin-top:16px", text: phase }),
      el("a", { class: "btn", style: "margin-top:12px", href: "#/papers", text: "先去看论文流 →" })
    ]));
  }

  function viewNews(r) {
    var M = window.AIVCNews;
    try {
      if (M && typeof M.render === "function") {
        var n = M.render(api, r);
        if (n && typeof n === "object" && n.nodeType === 1) return n;
        if (Array.isArray(n)) return el("div", {}, n);
      }
    } catch (e) { recordError("news: " + ((e && e.message) || e)); }
    return viewSoon("Business News", "商业新闻", "Phase 3", "商业新闻模块暂时无法加载，请刷新页面重试。");
  }

  function viewResources(r) {
    var M = window.AIVCResources;
    try {
      if (M && typeof M.render === "function") {
        var n = M.render(api, r);
        if (n && typeof n === "object" && n.nodeType === 1) return n;
        if (Array.isArray(n)) return el("div", {}, n);
      }
    } catch (e) { recordError("resources: " + ((e && e.message) || e)); }
    return viewSoon("Knowledge Resources", "知识资源", "Phase 4", "知识资源模块暂时无法加载，请刷新页面重试。");
  }

  // ---------- 路由 ----------
  var ROUTES = ["home", "papers", "topics", "map", "datasets", "weekly", "news", "resources", "about"];
  function parseHash() {
    var h = location.hash || "";
    if (h.charAt(0) === "#") h = h.slice(1);
    var qi = h.indexOf("?");
    var path = qi >= 0 ? h.slice(0, qi) : h;
    var qs = qi >= 0 ? h.slice(qi + 1) : "";
    var parts = path.split("/").filter(Boolean);
    return { name: parts[0] || "home", arg: parts[1] ? safeDecode(parts[1]) : "", parts: parts.map(safeDecode), params: new URLSearchParams(qs) };
  }
  function safeDecode(s) { try { return decodeURIComponent(s); } catch (e) { return s; } }

  function render() {
    var r = parseHash();
    if (ROUTES.indexOf(r.name) < 0) { location.replace("#/"); return; }
    var view;
    runCleanups();
    switch (r.name) {
      case "papers": case "topics": view = viewPapers(r.params); break;
      case "map": view = viewMap(); break;
      case "datasets": view = viewDatasets(); break;
      case "weekly": view = viewWeekly(r.arg); break;
      case "news": view = viewNews(r); break;
      case "about": view = viewAbout(); break;
      case "resources": view = viewResources(r); break;
      default: view = homeView();
    }
    clear(app);
    app.appendChild(view);
    setNavCurrent(r.name);
    var companyPage = r.name === "news" && r.parts[1] === "company" ? D.companyById[r.parts[2]] : null;
    document.title = companyPage ? str(companyPage.name) + " · 商业新闻 · 虚拟细胞雷达" : (r.name === "home" ? "" : ((r.name === "topics" ? "我的课题" : r.name === "about" ? "关于与常见问题" : navLabel(r.name)) + " · ")) + "虚拟细胞雷达";
    reveal(app);
    window.scrollTo(0, 0);
    updateNav();
    root.dataset.ready = "1";
    root.dataset.route = r.name;
    if (r.name === "topics") handleTopicsRoute();
  }
  function navLabel(n) {
    var a = document.querySelector('#nav a[data-route="' + n + '"]');
    return a ? (a.firstChild && a.firstChild.nodeType === 3 ? a.firstChild.nodeValue : a.textContent) : n;
  }

  function showError(msg) {
    clear(app);
    app.appendChild(el("div", { class: "wrap" }, el("div", { class: "card error-box" }, [
      el("div", { class: "eyebrow mono", text: "加载失败" }),
      el("h2", { text: "无法读取数据文件" }),
      el("p", { text: msg }),
      el("p", { class: "hint" }, [
        "请先运行 ", el("code", { text: "uv run python -m aivc build-site" }), "，再用 ", el("code", { text: "uv run python -m aivc serve" }),
        " 通过 http://localhost:8000 访问。直接用 file:// 打开页面时浏览器会禁止读取数据文件。"
      ])
    ])));
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(url + " 返回 HTTP " + r.status);
      return r.json();
    });
  }

  function getJSONSoft(url) {
    return getJSON(url).catch(function () { return null; });
  }

  // ---------- 公共动效层（配合 motion.css） ----------
  var viewTimeline = false;
  try { viewTimeline = !reduced && !!(window.CSS && CSS.supports && CSS.supports("animation-timeline: view()")); } catch (e) { /* ignore */ }
  if (viewTimeline) root.classList.add("mo-vt");
  function rise(node, opts) {
    if (!node) return node;
    opts = opts || {};
    node.classList.add("mo-rise");
    if (typeof opts.delay === "number") node.style.setProperty("--mo-delay", opts.delay + "ms");
    if (opts.start != null) node.style.setProperty("--mo-start", typeof opts.start === "number" ? opts.start + "%" : String(opts.start));
    if (opts.end != null) node.style.setProperty("--mo-end", typeof opts.end === "number" ? opts.end + "%" : String(opts.end));
    if (!viewTimeline && !reduced) watch(node, function (n) { n.classList.add("is-in"); }, null, { once: true, threshold: 0.12 });
    return node;
  }
  function play(node) { if (node) node.setAttribute("data-playing", ""); return node; }
  function unplay(node) { if (node) node.removeAttribute("data-playing"); return node; }
  function replay(node) {
    if (!node) return node;
    node.removeAttribute("data-playing");
    void node.offsetWidth;
    node.setAttribute("data-playing", "");
    return node;
  }
  function autoPause(node) {
    var inView = true;
    function apply() { node.classList.toggle("mo-paused", !(inView && !document.hidden)); }
    var stop = watch(node, function () { inView = true; apply(); }, function () { inView = false; apply(); }, { threshold: 0 });
    document.addEventListener("visibilitychange", apply);
    onCleanup(function () { document.removeEventListener("visibilitychange", apply); });
    return { active: function () { return inView && !document.hidden; }, stop: stop };
  }
  var toastRegion = null, toastTimer = 0, toastLeave = 0;
  function toast(text, opts) {
    opts = opts || {};
    var ms = typeof opts.duration === "number" ? opts.duration : 2400;
    if (!toastRegion || !toastRegion.isConnected) {
      toastRegion = el("div", { "class": "mo-toast-region", role: "status", "aria-live": "polite" });
      document.body.appendChild(toastRegion);
    }
    clearTimeout(toastTimer); clearTimeout(toastLeave);
    clear(toastRegion);
    var t = el("div", { "class": "mo-toast" });
    t.textContent = String(text == null ? "" : text);
    toastRegion.appendChild(t);
    function remove() { if (t.parentNode) t.parentNode.removeChild(t); }
    toastTimer = setTimeout(function () {
      if (reduced) { remove(); return; }
      t.classList.add("is-leaving");
      toastLeave = setTimeout(remove, 160);
    }, ms);
    return t;
  }

  var api = {
    el: el, clear: clear, arr: arr, str: str, tag: tag, fmtScore: fmtScore, safeUrl: safeUrl, extLink: extLink,
    taskLabel: taskLabel, parLabel: parLabel, circleLabel: circleLabel,
    paperCard: paperCard, openDrawer: openDrawer, reveal: reveal, D: D, byId: byId,
    openTopicsPanel: openTopicsPanel, activeTopics: activeTopics, topicsOf: topicsOf, onTopicsChange: onTopicsChange,
    motion: {
      reduced: reduced, onCleanup: onCleanup, watch: watch, countUp: countUp, sequence: sequence,
      ease: { out: "cubic-bezier(.22,1,.36,1)", std: "cubic-bezier(.4,0,.2,1)", wire: "cubic-bezier(.65,0,.35,1)", fade: "cubic-bezier(0,0,.2,1)" },
      viewTimeline: viewTimeline, rise: rise, play: play, unplay: unplay, replay: replay, autoPause: autoPause, toast: toast
    }
  };
  window.AIVC = api;

  function initNews(news, comp) {
    var n = news && typeof news === "object" ? news : {};
    D.news = { generated_at: n.generated_at, count: typeof n.count === "number" ? n.count : arr(n.items).length, event_types: arr(n.event_types),
      items: arr(n.items), weeks: arr(n.weeks), funding_quarters: arr(n.funding_quarters) };
    var c = comp && typeof comp === "object" ? comp : {};
    D.companies = { generated_at: c.generated_at, categories: (c.categories && typeof c.categories === "object") ? c.categories : {},
      companies: arr(c.companies), paper_companies: (c.paper_companies && typeof c.paper_companies === "object") ? c.paper_companies : {} };
    D.companyById = {};
    D.companies.companies.forEach(function (x) { if (x && x.id) D.companyById[x.id] = x; });
    D.paperCompanies = D.companies.paper_companies;
  }

  function initResources(res) {
    var r = res && typeof res === "object" ? res : {};
    D.resources = { generated_at: r.generated_at, metrics_fetched_at: r.metrics_fetched_at, count: typeof r.count === "number" ? r.count : arr(r.items).length,
      maintained_within_days: r.maintained_within_days, kinds: arr(r.kinds), sections: arr(r.sections), tasks: arr(r.tasks), paradigms: arr(r.paradigms), items: arr(r.items).filter(function (x) { return x && typeof x === "object" && x.id; }) };
    var RD = window.AIVCResourcesData;
    try {
      D.resByPaper = RD ? RD.buildPaperIndex(D.resources.items) : {};
      D.resByDataset = RD ? RD.buildDatasetIndex(D.resources.items) : {};
    } catch (e) { recordError("resources-index: " + ((e && e.message) || e)); D.resByPaper = {}; D.resByDataset = {}; }
  }

  function init() {
    Promise.all([getJSON("data/papers.json"), getJSON("data/meta.json"), getJSON("data/datasets.json"), getJSON("data/weekly.json"), getJSONSoft("data/home.json"), getJSONSoft("data/topics.json"), getJSONSoft("data/news.json"), getJSONSoft("data/companies.json"), getJSONSoft("data/resources.json")]).then(function (res) {
      D.home = (res[4] && typeof res[4] === "object") ? res[4] : null;
      D.papers = arr(res[0] && res[0].papers).filter(function (p) { return p && typeof p.id === "string" && p.id; });
      D.meta = res[1] || {};
      D.meta.stats = D.meta.stats || {};
      D.meta.taxonomy = D.meta.taxonomy || { tasks: [], paradigms: [] };
      D.meta.circle_labels = D.meta.circle_labels || {};
      D.datasets = res[2] || { new_datasets: [], used_top: [] };
      D.weekly = res[3] || { reports: [] };
      D.papers.forEach(function (p) { byId[p.id] = p; });
      initNews(res[6], res[7]);
      initResources(res[8]);
      initTopics(res[5]);
      var gen = document.getElementById("foot-gen");
      if (gen && D.meta.generated_at) gen.textContent = "数据生成于 " + String(D.meta.generated_at).replace("T", " ");
      window.addEventListener("hashchange", function () {
        // 路由切换时关掉还开着的详情抽屉，避免旧论文盖在新视图上
        // 例外：目标仍是 #/topics 时保留已打开的课题面板（面板的 close 事件是异步的，关掉再开会被它的收尾清掉）
        var toTopics = parseHash().name === "topics";
        document.querySelectorAll("dialog[open]").forEach(function (d) {
          if (toTopics && d.classList.contains("tp-panel")) return;
          d.close();
        });
        render();
      });
      render();
    }).catch(function (e) {
      showError((e && e.message) || String(e));
    });
  }

  try { init(); } catch (e) { recordError(e && e.message); showError((e && e.message) || String(e)); }
})();
