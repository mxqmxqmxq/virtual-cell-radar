/* AIVC 首页（落地页）。仅暴露 window.AIVCHome.render(api)。
 * 所有数据来自 api.D；所有文本经 api.el 的 text 输出，不使用 innerHTML。 */
(function () {
  "use strict";

  var WORDS = ["论文脉络", "范式演化", "新数据集", "行业动态", "每周动态"];
  var ROTATE_MS = 2600; // 参考站实测：切词间隔约 2.6s

  function render(api) {
    var el = api.el;
    var D = api.D || {};
    var meta = D.meta || {};
    var home = D.home || null;
    var papers = D.papers || [];
    var byId = api.byId || {};
    var M = api.motion || { reduced: true, onCleanup: function () {}, watch: function (n, e) { if (e) e(); }, countUp: function () {} };
    var reduced = !!M.reduced;
    var stats = meta.stats || {};
    var prog = (home && home.progress) || null;

    // ---------- 小工具 ----------
    function safe(name, fn) {
      try { return fn(); } catch (e) { if (window.console) console.warn("[AIVCHome] 区块失败：" + name, e); return null; }
    }
    function num(v) { return typeof v === "number" && isFinite(v) ? v : 0; }
    function fmt(n) { try { return Number(n).toLocaleString("en-US"); } catch (e) { return String(n); } }
    function sv(node, name, val) { node.style.setProperty(name, String(val)); return node; }
    function on(node, cls) { node.classList.add(cls); }
    function once(node, cls, delay) {
      // 进入视口时加类；reduced 下无需
      M.watch(node, function () {
        if (delay) { var t = setTimeout(function () { on(node, cls); }, delay); M.onCleanup(function () { clearTimeout(t); }); }
        else on(node, cls);
      }, null, { threshold: 0.2, once: true });
    }
    function rise(node, o) {
      try { if (M.rise) M.rise(node, o || {}); } catch (e) { /* 缺失时直接显示 */ }
      return node;
    }
    function ext(name, method) {
      var m = window[name];
      if (m && typeof m[method] === "function") return safe(name + "." + method, function () { return m[method](api); });
      return null;
    }
    // 背景层：优先交给 AIVCGlow；缺失或失败时回退到 CSS 光晕
    function buildBg(twoGlows) {
      var bg = el("div", { class: "hm-bg", "aria-hidden": "true" });
      var mounted = false;
      // 首屏优先挂 LED 像素隧道（WebGL2）；不可用时回退到渐变光晕，再不行用 CSS 光晕
      if (twoGlows && window.AIVCTunnel && typeof window.AIVCTunnel.mount === "function") {
        try { mounted = window.AIVCTunnel.mount(bg, api) === true; } catch (e) { mounted = false; bg.textContent = ""; }
      }
      if (!mounted && window.AIVCGlow && typeof window.AIVCGlow.mount === "function") {
        try { mounted = window.AIVCGlow.mount(bg, api) !== false; } catch (e) { mounted = false; bg.textContent = ""; }
      }
      if (!mounted) {
        bg.appendChild(el("div", { class: "hm-glow hm-glow-a" }));
        if (twoGlows) bg.appendChild(el("div", { class: "hm-glow hm-glow-b" }));
      }
      bg.appendChild(el("div", { class: "hm-dots" }));
      return bg;
    }
    function play(n) { if (M.play) M.play(n); else n.setAttribute("data-playing", ""); }
    function link(href, cls, text, extra) {
      var a = el("a", { class: cls, href: href }, text);
      if (extra) extra(a);
      return a;
    }
    function head(eyebrow, plain, hl, btn) {
      return rise(el("div", { class: "hm-head" }, [
        el("div", { class: "hm-head-t" }, [
          eyebrow ? el("div", { class: "hm-eyebrow mono", text: eyebrow }) : null,
          el("h2", { class: "hm-h2" }, [plain, hl ? el("span", { class: "hl-text hm-hl", text: hl }) : null])
        ]),
        btn || null
      ]), { delay: 0 });
    }
    function sec(cls, label, children) {
      return el("section", { class: "hm-sec " + cls, "aria-label": label }, children);
    }

    // ---------- 1 首屏 ----------
    function buildHero() {
      var total = prog ? num(prog.total) : num(stats.total);
      var scored = prog ? num(prog.scored) : num(stats.scored);
      var latest = (D.weekly && D.weekly.reports && D.weekly.reports[0]) || null;
      var lw = (home && home.weekly) || meta.latest_weekly || latest;
      var news7 = prog ? num(prog.news_7d) : num(stats.news_7d);
      var hasNewsSite = !!(D.news && D.news.items && D.news.items.length);
      var kpis = [
        { v: total, label: "论文总数 · Papers" },
        { v: prog ? num(prog.this_week_new) : num(stats.this_week_new), label: "本周新增 · This week" },
        { v: prog ? num(prog.must_read) : num(stats.must_read), label: "必读 · Must-read" },
        news7 > 0
          ? { v: news7, label: "近 7 天行业事件 · News" }
          : { v: prog ? num(prog.new_datasets) : num(stats.new_datasets), label: "新数据集 · Datasets" }
      ];

      // 轮换词槽
      var words = WORDS.map(function (w, i) { return el("span", { class: "hm-w" + (i === 0 ? " is-on" : ""), text: w }); });
      var measure = el("span", { class: "hm-measure", "aria-hidden": "true" }, WORDS.map(function (w) { return el("span", { text: w }); }));
      var slot = el("span", { class: "hm-slot", "aria-hidden": "true" }, words.concat([measure]));
      var mark = el("span", { class: "hm-mark", "aria-hidden": "true" }, [el("span", { class: "hm-mark-t", text: "整理成一张地图" })]);
      var line1 = el("span", { class: "hm-line hm-rise", "aria-hidden": "true", text: "把虚拟细胞领域的" });
      var line2 = el("span", { class: "hm-line hm-l2 hm-rise", "aria-hidden": "true" }, [slot, mark]);
      sv(line1, "--i", 1); sv(line2, "--i", 2);
      var h1 = el("h1", { class: "hm-title" }, [
        el("span", { class: "hm-sr", text: "把虚拟细胞领域的研究动态整理成一张地图" }),
        line1, line2
      ]);

      var cta2 = hasNewsSite
        ? link("#/news", "hm-btn hm-btn-ghost", [el("span", { text: "行业动态" }), el("i", { class: "hm-arr", text: "→" })])
        : (lw && lw.id
          ? link("#/weekly/" + encodeURIComponent(lw.id), "hm-btn hm-btn-ghost", [el("span", { text: "阅读最新周报" }), el("i", { class: "hm-arr", text: "→" })])
          : link("#/map", "hm-btn hm-btn-ghost", [el("span", { text: "范式地图" }), el("i", { class: "hm-arr", text: "→" })]));
      var cta1 = link("#/papers", "hm-btn hm-btn-solid", [el("span", { text: "浏览论文流" }), el("i", { class: "hm-arr", text: "→" })]);
      // 悬停主按钮 50ms 后让隧道加速（参考站 R-HERO-02），移开立即回落
      if (window.AIVCTunnel && typeof window.AIVCTunnel.setBoost === "function") {
        var boostT = 0;
        cta1.addEventListener("pointerenter", function () { clearTimeout(boostT); boostT = setTimeout(function () { window.AIVCTunnel.setBoost(true); }, 50); });
        cta1.addEventListener("pointerleave", function () { clearTimeout(boostT); window.AIVCTunnel.setBoost(false); });
        M.onCleanup(function () { clearTimeout(boostT); window.AIVCTunnel.setBoost(false); });
      }
      var ctas = el("div", { class: "hm-cta-row hm-rise" }, [cta1, cta2]);
      sv(ctas, "--i", 5);

      var kpiNums = [];
      var kpiRow = el("div", { class: "hm-kpis hm-rise" }, kpis.map(function (k) {
        var n = el("div", { class: "hm-kpi-n", text: reduced ? fmt(k.v) : "0" });
        kpiNums.push({ node: n, v: k.v });
        return el("div", { class: "hm-kpi" }, [n, el("div", { class: "hm-kpi-l mono", text: k.label })]);
      }));
      sv(kpiRow, "--i", 6);

      var ratio = total > 0 ? Math.min(1, Math.max(0.02, scored / total)) : 0;
      var allDone = total > 0 && scored >= total;
      var fill = el("div", { class: "hm-prog-fill" });
      sv(fill, "--p", ratio);
      var prog_el = el("div", { class: "hm-prog hm-rise", role: "img", "aria-label": "LLM 打分进度 " + scored + " / " + total }, [
        el("div", { class: "hm-prog-t mono" }, [
          el("span", { text: "LLM 打分" }),
          el("span", { text: allDone ? "全部已打分 · " + fmt(total) : "已打分 " + fmt(scored) + " / " + fmt(total) })
        ]),
        el("div", { class: "hm-prog-track" }, fill)
      ]);
      sv(prog_el, "--i", 7);

      var inner = el("div", { class: "hm-hero-in" }, [
        el("div", { class: "hm-eyebrow hm-eyebrow-hero mono hm-rise" }, [
          el("i", { class: "hm-live mo-pulse", "aria-hidden": "true" }),
          el("span", { text: "AIVC · Virtual Cell Radar" })
        ]),
        h1,
        el("p", { class: "hm-lede hm-rise", text: "每日抓取 arXiv、bioRxiv、OpenReview 等来源，经关键词预筛、去重与 LLM 打分，归类到任务与范式；并跟踪行业融资、合作与模型发布，汇总成论文流、范式地图、数据集表、行业动态与每周简报。" }),
        ctas, kpiRow, prog_el
      ]);
      sv(inner.children[2], "--i", 3);
      sv(inner.children[0], "--i", 0);

      var hero = el("section", { class: "hm-hero", "data-nav-theme": "dark", "aria-label": "AIVC 概览" }, [
        buildBg(true),
        inner
      ]);

      // KPI 计数 / 进度条
      once(prog_el, "is-on");
      if (!reduced) {
        M.watch(kpiRow, function () {
          kpiNums.forEach(function (k) {
            try { M.countUp(k.node, k.v, { duration: 1200, format: fmt }); } catch (e) { k.node.textContent = fmt(k.v); }
          });
        }, null, { threshold: 0.4, once: true });
        // 兜底：countUp 未接管时也不能停在 0
        var fb = setTimeout(function () {
          kpiNums.forEach(function (k) { if (k.node.textContent === "0" && k.v) k.node.textContent = fmt(k.v); });
        }, 4000);
        M.onCleanup(function () { clearTimeout(fb); });
      }

      // 进入动画
      var r1, r2, ft;
      function enter() { on(hero, "is-in"); }
      if (reduced) { enter(); }
      else {
        r1 = requestAnimationFrame(function () { r2 = requestAnimationFrame(enter); });
        ft = setTimeout(enter, 300);
        M.onCleanup(function () { cancelAnimationFrame(r1); cancelAnimationFrame(r2); clearTimeout(ft); });
      }

      // 光晕暂停（离开视口/页面隐藏）
      var visible = true;
      function syncPause() { hero.classList.toggle("hm-paused", !visible || document.hidden); }
      M.watch(hero, function () { visible = true; syncPause(); }, function () { visible = false; syncPause(); }, { threshold: 0 });
      document.addEventListener("visibilitychange", syncPause);
      M.onCleanup(function () { document.removeEventListener("visibilitychange", syncPause); });

      // 词槽宽度测量 + 轮换
      var widths = [];
      function measureAll() {
        var ok = false;
        for (var i = 0; i < words.length; i++) {
          var w = measure.children[i].getBoundingClientRect().width;
          if (w > 0) { widths[i] = Math.ceil(w) + 2; ok = true; }
        }
        if (ok) slot.style.width = (widths[idx] || widths[0]) + "px";
      }
      var idx = 0;
      if (!reduced) {
        var mt = setTimeout(measureAll, 60);
        var mt2 = setTimeout(measureAll, 600);
        M.onCleanup(function () { clearTimeout(mt); clearTimeout(mt2); });
        window.addEventListener("resize", measureAll);
        M.onCleanup(function () { window.removeEventListener("resize", measureAll); });
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(measureAll, function () {});
        var iv = setInterval(function () {
          if (hero.classList.contains("hm-paused")) return;
          var prev = idx, next = (idx + 1) % words.length;
          slot.classList.add("hm-nt");
          for (var i = 0; i < words.length; i++) if (i !== prev) words[i].classList.remove("is-out");
          void slot.offsetWidth;
          slot.classList.remove("hm-nt");
          words[prev].classList.remove("is-on"); words[prev].classList.add("is-out");
          words[next].classList.add("is-on");
          idx = next;
          if (widths[idx]) slot.style.width = widths[idx] + "px";
        }, ROTATE_MS);
        M.onCleanup(function () { clearInterval(iv); });
      }
      return hero;
    }

    // ---------- D 它如何工作：数据来源芯片 + 演示舞台 ----------
    function buildSourceChips() {
      var list = (home && home.sources) || [];
      if (!list.length) return null;
      return rise(el("div", { class: "hm-srcs", role: "list", "aria-label": "数据来源" }, list.map(function (s) {
        var planned = s.status === "planned";
        var kids = [
          el("i", { class: "hm-mk hm-mk-" + (s.kind || "source"), "aria-hidden": "true" }),
          el("span", { class: "hm-chip-l", text: String(s.label || s.id || "") })
        ];
        if (planned) kids.push(el("span", { class: "hm-chip-n mono", text: "计划中" }));
        else if (num(s.count) > 0) kids.push(el("span", { class: "hm-chip-n mono", text: fmt(s.count) + " 篇" }));
        return el("span", { class: "hm-chip" + (planned ? " is-planned" : ""), role: "listitem" }, kids);
      })), { delay: 0 });
    }
    function buildHow() {
      var kids = [head("Sources & method", "数据怎么来，", "可信吗", null)];
      var chips = buildSourceChips();
      if (chips) kids.push(chips);
      var demos = ext("AIVCDemos", "section");
      if (demos) kids.push(demos);
      if (!chips && !demos) return null;
      kids.push(el("div", { class: "hm-how-more" }, link("#/about", "btn", "完整流程、命令行与常见问题 →")));
      return sec("hm-how", "它如何工作", kids);
    }

    // ---------- E 结尾：让它为我所用 ----------
    function buildCta() {
      var gen = meta.generated_at ? String(meta.generated_at) : "";
      var lw = (home && home.weekly) || meta.latest_weekly || null;
      var arrow = function () { return el("i", { class: "hm-arr", text: "→" }); };
      var main;
      if (typeof api.openTopicsPanel === "function") {
        main = el("button", { class: "hm-btn hm-btn-solid", type: "button" }, [el("span", { text: "设置我的课题" }), arrow()]);
        main.addEventListener("click", function () { if (!api.openTopicsPanel(main)) location.hash = "#/papers"; });
      } else main = link("#/papers", "hm-btn hm-btn-solid", [el("span", { text: "浏览论文流" }), arrow()]);
      var row = [main, link("#/papers", "hm-btn hm-btn-ghost", [el("span", { text: "论文流" }), arrow()])];
      row.push(link("#/news", "hm-btn hm-btn-ghost", [el("span", { text: "行业动态" }), arrow()]));
      row.push(link("#/resources", "hm-btn hm-btn-ghost", [el("span", { text: "知识资源" }), arrow()]));
      row.push(link(lw && lw.id ? "#/weekly/" + encodeURIComponent(lw.id) : "#/weekly", "hm-btn hm-btn-ghost", [el("span", { text: "周报" }), arrow()]));
      var cta = el("section", { class: "hm-cta", "data-nav-theme": "dark", "aria-label": "让它为我所用" }, [
        buildBg(false),
        el("div", { class: "hm-cta-in" }, [
          el("h2", { class: "hm-cta-h hm-rise" }, ["让它为", el("span", { class: "hm-mark" }, el("span", { class: "hm-mark-t", text: "你的课题" })), "服务"]),
          el("p", { class: "hm-cta-p hm-rise", text: "写下几个关键词，论文流、周报和本周速览就会把命中你课题的条目排在前面。课题只保存在本机浏览器里。" }),
          el("div", { class: "hm-cta-row hm-rise" }, row),
          gen ? el("div", { class: "hm-gen mono", text: "数据生成于 " + gen }) : null
        ])
      ]);
      sv(cta.querySelector(".hm-cta-p"), "--i", 1);
      sv(cta.querySelector(".hm-cta-row"), "--i", 2);
      M.watch(cta, function () { on(cta, "is-in"); }, null, { threshold: 0.25, once: true });
      return cta;
    }

    // ---------- 组装：首屏 → 轨道 [本周速览、领域脉搏、它如何工作] → 结尾 ----------
    var root = el("div", { class: "hm-home" });
    function add(n) { if (n) root.appendChild(n); }
    add(safe("hero", buildHero));
    var rails = el("div", { class: "hm-rails" });
    function addR(n) { if (n) rails.appendChild(n); }
    addR(ext("AIVCDigest", "digest"));
    addR(ext("AIVCDigest", "pulse"));
    addR(safe("how", buildHow));
    root.appendChild(rails);
    add(safe("cta", buildCta));
    return root;
  }

  window.AIVCHome = { render: render };
})();
