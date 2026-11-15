/* AIVC 首页「本周速览」与「领域脉搏」两个区块。
 * 仅暴露 window.AIVCDigest = { digest(api), pulse(api) }，各返回 section 元素或 null，内部异常自行捕获。
 * 数据：home.json 的 digest / heat / weeks，news.json（api.D.news）。所有文本经 api.el 的 text 输出，不使用 innerHTML。
 * 动效只用 api.motion 与 motion.css 现有类（mo-rise / mo-reveal / mo-pop / mo-bar / mo-grow-y）。 */
(function () {
  "use strict";

  var ROWS_PAPERS = 8, ROWS_NEWS = 5, ROWS_DS = 4, ROWS_MOV = 5, MAX_QUARTERS = 8;
  var TYPE_FALLBACK = { funding: "融资", partnership: "合作", m_and_a: "并购", model_or_data_release: "模型或数据发布", clinical: "临床进展", people: "重要人事", other: "其他" };

  function num(v) { return typeof v === "number" && isFinite(v) ? v : 0; }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function str(v) { return v == null ? "" : String(v); }
  function sv(node, name, val) { node.style.setProperty(name, String(val)); return node; }

  function ctx(api) {
    var M = api.motion || { reduced: true, onCleanup: function () {}, watch: function (n, e) { if (e) e(); } };
    return {
      api: api, el: api.el, D: api.D || {}, M: M, reduced: !!M.reduced,
      rise: function (n, o) { try { if (M.rise) M.rise(n, o || {}); } catch (e) { /* 缺失时直接显示 */ } return n; },
      play: function (n) { if (M.play) M.play(n); else n.setAttribute("data-playing", ""); }
    };
  }
  function head(c, eyebrow, plain, hl, btn) {
    var el = c.el;
    return c.rise(el("div", { class: "hm-head" }, [
      el("div", { class: "hm-head-t" }, [
        eyebrow ? el("div", { class: "hm-eyebrow mono", text: eyebrow }) : null,
        el("h2", { class: "hm-h2" }, [plain, hl ? el("span", { class: "hl-text hm-hl", text: hl }) : null])
      ]),
      btn || null
    ]), { delay: 0 });
  }
  function more(c, href, text) { return c.el("a", { class: "dg-more", href: href, text: text }); }

  // ===================================================================
  // B 本周速览
  // ===================================================================
  function digest(api) {
    try { return buildDigest(api); }
    catch (e) { if (window.console) console.warn("[AIVCDigest] 速览构建失败", e); return null; }
  }

  function buildDigest(api) {
    var c = ctx(api), el = c.el, D = c.D, M = c.M;
    var home = D.home || null;
    var dg = home && home.digest && typeof home.digest === "object" ? home.digest : null;
    var byId = api.byId || {};
    var NEWS = D.news || { items: [], event_types: [] };
    var typeLabel = {};
    Object.keys(TYPE_FALLBACK).forEach(function (k) { typeLabel[k] = TYPE_FALLBACK[k]; });
    arr(NEWS.event_types).forEach(function (t) { if (t && t.id) typeLabel[t.id] = t.label; });
    var ND = window.AIVCNewsData || null;
    function usd(v) { return ND && ND.fmtUsd ? ND.fmtUsd(v) : "$" + Math.round(num(v) / 1e6) + "M"; }

    // ---------- 数据装配（缺 digest 时降级） ----------
    var papers = [];
    if (dg) {
      arr(dg.paper_ids).forEach(function (id) { if (byId[id]) papers.push(byId[id]); });
    } else if (home && home.featured) {
      arr(home.featured.items).forEach(function (it) { if (it && byId[it.id]) papers.push(byId[it.id]); });
    }
    if (!papers.length && !dg) {
      papers = arr(D.papers).slice().sort(function (a, b) {
        function s(p) { return p.scored && typeof p.final_score === "number" ? 1000 + p.final_score : num(p.keyword_score); }
        return s(b) - s(a);
      }).slice(0, 12);
    }
    var newsItems = [];
    var allNews = arr(NEWS.items);
    if (dg) {
      var nById = {};
      allNews.forEach(function (n) { if (n && n.id) nById[n.id] = n; });
      arr(dg.news_ids).forEach(function (id) { if (nById[id]) newsItems.push(nById[id]); });
    } else {
      newsItems = allNews.filter(function (n) { return n && n.scored !== false; }).slice(0, 8);
    }
    var datasets = dg ? arr(dg.new_datasets) : arr(D.datasets && D.datasets.new_datasets);
    var movers = dg ? arr(dg.movers) : [];
    var win = dg && dg.window ? dg.window : null;
    var cw = (D.meta && D.meta.current_week) || {};
    var weekly = (dg && dg.weekly) || (home && home.weekly) || null;
    var nPapers = dg ? num(dg.n_papers) : papers.length;
    var nMust = dg ? num(dg.n_must_read) : papers.filter(function (p) { return p.must_read; }).length;
    var nNews = dg ? num(dg.n_news_7d) : num(home && home.progress && home.progress.news_7d);
    var nDs = datasets.length;

    // ---------- 头部 ----------
    var eyebrow = win ? str(win.label) : (cw.id ? "本周 " + cw.id + " · " + str(cw.start) + " ~ " + str(cw.end) : "This week");
    var headBtn = weekly && weekly.id ? el("a", { class: "btn", href: "#/weekly/" + encodeURIComponent(weekly.id), text: "阅读周报全文 →" }) : null;
    var kids = [head(c, eyebrow, "本周", "速览", headBtn)];
    if (dg && dg.fallback) kids.push(el("div", { class: "notice", text: "近 7 天新增不足，显示最近入库。" }));
    else if (!dg) kids.push(el("div", { class: "notice", text: "数据文件缺少速览统计，当前按评分显示精选论文。重新运行 build-site 可更新。" }));

    // 概要数字行
    function stat(n, l) { return el("div", { class: "dg-stat" }, [el("span", { class: "dg-stat-n", text: String(n) }), el("span", { class: "dg-stat-l mono", text: l })]); }
    kids.push(c.rise(el("div", { class: "dg-stats", role: "group", "aria-label": "本周概要" }, [
      stat(nPapers, "论文"), stat(nMust, "必读"), stat(nNews, "行业事件"), stat(nDs, "新数据集")
    ]), { delay: 0 }));

    // ---------- 左栏：论文 ----------
    var papersBox = el("div", { class: "dg-papers" });
    var segBox = el("div", { class: "dg-segwrap" });
    var listHost = el("div", { class: "dg-listhost" });
    var choice = null; // 访客手动选择的视图；null 表示跟随默认
    var animateRows = true;

    function actTopics() { try { return api.activeTopics ? arr(api.activeTopics()) : []; } catch (e) { return []; } }
    function hitsOf(p) { try { return api.topicsOf ? arr(api.topicsOf(p)) : []; } catch (e) { return []; } }
    function hasHits(act) {
      if (!act.length) return false;
      for (var i = 0; i < papers.length; i++) if (hitsOf(papers[i]).length) return true;
      return false;
    }
    function drawer(p, btn) { if (api.openDrawer) api.openDrawer(p, btn); }

    function paperRow(p, i) {
      var hits = hitsOf(p);
      var sc = p.scored && typeof p.final_score === "number" ? api.fmtScore(p.final_score) : "—";
      var title = el("button", { class: "dg-title", type: "button", text: str(p.title) });
      title.addEventListener("click", function () { drawer(p, title); });
      var sub = str(p.novelty_zh) || ([str(p.source), str(p.date || p.published)].filter(Boolean).join(" · "));
      var tags = hits.slice(0, 2).map(function (h) { return el("span", { class: "tag tag-hl", text: "课题 · " + str(h.name) }); });
      var row = el("li", { class: "dg-row mo-reveal" }, [
        el("span", { class: "dg-rank mono", text: (i < 9 ? "0" : "") + (i + 1) }),
        el("div", { class: "dg-main" }, [
          el("div", { class: "dg-line" }, [
            el("span", { class: "dg-score mono" + (p.must_read ? " is-must" : ""), title: p.must_read ? "必读" : "最终分", text: sc }),
            title
          ]),
          sub ? el("div", { class: "dg-sub", text: sub }) : null,
          tags.length ? el("div", { class: "tags dg-tags" }, tags) : null
        ])
      ]);
      if (animateRows) sv(row, "--mo-delay", (i * 50) + "ms");
      return row;
    }

    function drawPapers() {
      var act = actTopics();
      var hit = hasHits(act);
      var mode = choice || (hit ? "mine" : "all");
      if (!act.length) mode = "all";
      // 分段控件 / 引导
      api.clear(segBox);
      if (act.length) {
        var bAll = el("button", { class: "dg-seg-b", type: "button", "aria-pressed": String(mode === "all"), text: "全部" });
        var bMine = el("button", { class: "dg-seg-b", type: "button", "aria-pressed": String(mode === "mine"), text: "我的课题" });
        bAll.addEventListener("click", function () { choice = "all"; drawPapers(); });
        bMine.addEventListener("click", function () { choice = "mine"; drawPapers(); });
        segBox.appendChild(el("div", { class: "dg-seg", role: "group", "aria-label": "论文排序" }, [bAll, bMine]));
      } else if (api.openTopicsPanel) {
        var g = el("button", { class: "dg-guide", type: "button", text: "设置我的课题，速览按你的课题排序" });
        g.addEventListener("click", function () { api.openTopicsPanel(g); });
        segBox.appendChild(g);
      }
      // 列表
      var list = papers.slice();
      if (mode === "mine" && act.length) {
        var hitList = [], rest = [];
        list.forEach(function (p) { (hitsOf(p).length ? hitList : rest).push(p); });
        list = hitList.concat(rest);
      }
      list = list.slice(0, ROWS_PAPERS);
      api.clear(listHost);
      if (!list.length) {
        listHost.appendChild(el("div", { class: "dg-empty", text: "近期还没有入库论文。运行抓取与打分后这里会出现排行。" }));
      } else {
        var ol = el("ol", { class: "dg-rows" }, list.map(paperRow));
        listHost.appendChild(ol);
        if (mode === "mine" && act.length && !hit) listHost.insertBefore(el("div", { class: "hint dg-hint", text: "速览里暂时没有命中你课题的论文，已按默认排序显示。" }), ol);
      }
      animateRows = false;
    }
    drawPapers();
    papersBox.appendChild(el("div", { class: "dg-colhead" }, [el("h3", { class: "dg-h3", text: "论文" }), segBox]));
    papersBox.appendChild(listHost);
    papersBox.appendChild(more(c, "#/papers", "查看全部 →"));
    try { if (api.onTopicsChange) api.onTopicsChange(drawPapers); } catch (e) { /* ignore */ }

    // ---------- 右栏：行业动态 / 新数据集 / 趋势变化 ----------
    function blockHead(title, href, linkText) {
      return el("div", { class: "dg-colhead" }, [el("h3", { class: "dg-h3", text: title }), linkText ? el("a", { class: "dg-more dg-more-top", href: href, text: linkText }) : null]);
    }

    // 课题词命中新闻：只调用 matchTerms（不碰论文缓存）
    function newsHitsTopic(n, act) {
      var T = window.AIVCTopics;
      if (!T || typeof T.matchTerms !== "function") return false;
      var hay = [str(n.title), str(n.summary_zh), arr(n.companies).join(" ")].join(" ");
      for (var i = 0; i < act.length; i++) {
        try { if (T.matchTerms(hay, arr(act[i].terms))) return true; } catch (e) { /* 匹配失败按原序 */ }
      }
      return false;
    }
    var newsBox = el("div", { class: "dg-blk dg-news" });
    function newsRow(n) {
      var link = api.extLink ? api.extLink(n.url, str(n.title), "dg-ntitle") : null;
      if (!link) link = el("span", { class: "dg-ntitle", text: str(n.title) });
      var meta = [];
      var cos = arr(n.companies).slice(0, 2).join("、");
      if (cos) meta.push(cos);
      if (typeof n.amount_usd === "number" && n.amount_usd > 0) meta.push(usd(n.amount_usd));
      if (n.source) meta.push(str(n.source));
      return el("li", { class: "dg-nrow mo-reveal" }, [
        el("span", { class: "dg-ndate mono", text: str(n.date).slice(5) }),
        el("div", { class: "dg-nmain" }, [
          el("div", { class: "dg-nline" }, [
            n.major ? el("span", { class: "dg-major", title: "重大事件", text: "重大" }) : null,
            el("span", { class: "dg-ntype mono", text: typeLabel[n.event_type] || str(n.event_type) || "其他" })
          ]),
          link,
          meta.length ? el("div", { class: "dg-nmeta", text: meta.join(" · ") }) : null
        ])
      ]);
    }
    function drawNews() {
      api.clear(newsBox);
      newsBox.appendChild(blockHead("行业动态", "#/news", "全部行业动态 →"));
      var list = newsItems.slice();
      var act = actTopics();
      var decorated = list.map(function (n, i) { return { n: n, i: i, hit: act.length ? newsHitsTopic(n, act) : false }; });
      decorated.sort(function (a, b) {
        var am = a.n.major ? 1 : 0, bm = b.n.major ? 1 : 0;
        if (am !== bm) return bm - am;
        if (a.hit !== b.hit) return a.hit ? -1 : 1;
        return a.i - b.i;
      });
      list = decorated.map(function (d) { return d.n; }).slice(0, ROWS_NEWS);
      if (!list.length) {
        newsBox.appendChild(el("div", { class: "dg-empty" }, ["暂无行业动态。运行 ", el("code", { text: "uv run python -m aivc news" }), " 抓取。"]));
      } else {
        newsBox.appendChild(el("ul", { class: "dg-nrows" }, list.map(function (n, i) {
          var r = newsRow(n); sv(r, "--mo-delay", (i * 50) + "ms"); return r;
        })));
      }
    }
    drawNews();
    try { if (api.onTopicsChange) api.onTopicsChange(drawNews); } catch (e) { /* ignore */ }

    var dsBox = el("div", { class: "dg-blk" }, [blockHead("新数据集", "#/datasets", "全部数据集 →")]);
    var dsList = datasets.slice(0, ROWS_DS);
    if (!dsList.length) {
      dsBox.appendChild(el("div", { class: "dg-empty" }, ["近 7 天没有论文发布新数据集。", el("a", { href: "#/datasets", text: "查看全部数据集" })]));
    } else {
      dsBox.appendChild(el("ul", { class: "dg-drows" }, dsList.map(function (d, i) {
        var p = byId[d.paper_id];
        var ttl = str(d.paper_title || (p && p.title));
        var t;
        if (p && api.openDrawer) {
          t = el("button", { class: "dg-dpaper", type: "button", text: ttl });
          t.addEventListener("click", function () { drawer(p, t); });
        } else t = el("span", { class: "dg-dpaper", text: ttl });
        var r = el("li", { class: "dg-drow mo-reveal" }, [el("div", { class: "dg-dname", text: d.name ? str(d.name) : "（未命名）" }), t]);
        sv(r, "--mo-delay", (i * 50) + "ms");
        return r;
      })));
    }

    var movBox = el("div", { class: "dg-blk" }, [blockHead("趋势变化", "#/map", "范式地图 →")]);
    var mv = movers.slice(0, ROWS_MOV);
    if (!mv.length) {
      movBox.appendChild(el("div", { class: "dg-empty" }, ["已打分论文样本不足，暂无本期与上期的对比。", el("a", { href: "#/map", text: "查看范式地图" })]));
    } else {
      var cmax = 0; mv.forEach(function (m) { if (num(m.cur) > cmax) cmax = num(m.cur); });
      movBox.appendChild(el("ul", { class: "dg-mrows" }, mv.map(function (m, i) {
        var d = num(m.delta);
        var up = d > 0, down = d < 0;
        var fill = el("div", { class: "dg-mfill mo-bar" + (down ? " is-down" : "") });
        sv(fill, "--w", cmax > 0 ? Math.max(0.04, num(m.cur) / cmax).toFixed(3) : 0.04);
        sv(fill, "--mo-delay", (i * 60) + "ms");
        var r = el("li", { class: "dg-mrow", title: str(m.label) + "：本期 " + num(m.cur) + " 篇，上期 " + num(m.prev) + " 篇" }, [
          el("span", { class: "dg-mlabel", text: str(m.label) }),
          el("div", { class: "dg-mtrack" }, fill),
          el("span", { class: "dg-mn mono", text: String(num(m.cur)) }),
          el("span", { class: "dg-md mono" + (up ? " is-up" : down ? " is-down" : ""), text: (up ? "▲ +" : down ? "▼ −" : "– ") + Math.abs(d) })
        ]);
        return r;
      })));
      movBox.appendChild(el("div", { class: "hint dg-mnote", text: "本期（近 7 天）与前 7 天相比，按范式统计已打分论文篇数。" }));
    }

    var side = el("div", { class: "dg-side" }, [newsBox, dsBox, movBox]);
    var body = el("div", { class: "dg-body", "data-mo-demo": "" }, [papersBox, side]);
    sv(body, "--mo-duration", ".7s");
    c.rise(body, { start: 0, end: 18 });
    M.watch(body, function () { c.play(body); }, null, { threshold: 0.1, once: true });
    kids.push(body);
    return el("section", { class: "hm-sec dg", "aria-label": "本周速览" }, kids);
  }

  // ===================================================================
  // C 领域脉搏：范式 × 任务热力图 + 季度融资
  // ===================================================================
  function pulse(api) {
    try { return buildPulse(api); }
    catch (e) { if (window.console) console.warn("[AIVCDigest] 脉搏构建失败", e); return null; }
  }

  function buildPulse(api) {
    var c = ctx(api), el = c.el, D = c.D, M = c.M;
    var home = D.home || null;
    var h = home && home.heat;
    var weeks = arr(home && home.weeks);
    var NEWS = D.news || {};
    var ND = window.AIVCNewsData || null;

    // ---------- 左：热力图（沿用旧实现） ----------
    var heatGrid = null, heatDone = 0;
    var leftKids;
    if (h && num(h.max) > 0 && arr(h.paradigms).length && arr(h.tasks).length) {
      var cellMap = {};
      arr(h.cells).forEach(function (cl) { cellMap[cl.p + "|" + cl.t] = num(cl.n); });
      var T = h.tasks.length;
      var grid = el("div", { class: "hm-heat", role: "group", "aria-label": "范式 × 任务热力图" });
      grid.style.gridTemplateColumns = "minmax(84px, 150px) repeat(" + T + ", 28px)";
      grid.appendChild(el("span", { class: "hm-corner" }));
      h.tasks.forEach(function (t) { grid.appendChild(el("span", { class: "hm-col mono", title: t.label, text: t.label })); });
      h.paradigms.forEach(function (p, r) {
        grid.appendChild(el("span", { class: "hm-row-l", title: p.label, text: p.label }));
        h.tasks.forEach(function (t, ci) {
          var n = cellMap[p.id + "|" + t.id] || 0;
          var a = n > 0 ? 0.18 + 0.82 * n / h.max : 0;
          var cell;
          if (n > 0) {
            cell = el("a", {
              class: "hm-cell mo-pop", href: "#/papers?paradigm=" + encodeURIComponent(p.id) + "&task=" + encodeURIComponent(t.id),
              "aria-label": p.label + " × " + t.label + "：" + n + " 篇", title: p.label + " × " + t.label + "：" + n + " 篇"
            });
            cell.style.background = "rgba(var(--heat-rgb, 31, 79, 255), " + a.toFixed(3) + ")";
          } else cell = el("span", { class: "hm-cell is-zero" });
          sv(cell, "--mo-delay", ((r + ci) * 30) + "ms");
          grid.appendChild(cell);
        });
      });
      heatGrid = grid;
      heatDone = (h.paradigms.length + T) * 30 + 460;
      leftKids = [
        el("div", { class: "hm-panel-t mono", text: "研究：范式 × 任务 · 已打分 " + num(h.n_scored) + " 篇" }),
        el("div", { class: "hm-heat-wrap", tabindex: "0", role: "region", "aria-label": "范式 × 任务热力图（可横向滚动）" }, grid)
      ];
    } else {
      leftKids = [
        el("div", { class: "hm-panel-t mono", text: "研究：范式 × 任务" }),
        el("div", { class: "hm-hint-box", text: "热力图来自已打分论文的任务/范式标注，目前还没有可显示的数据。LLM 打分完成后会在这里逐格点亮。" })
      ];
    }
    var wmax = 0; weeks.forEach(function (w) { if (num(w.count) > wmax) wmax = num(w.count); });
    if (weeks.length && wmax > 0) {
      var cols = weeks.map(function (w, i) {
        var b = el("div", { class: "hm-wk-bar mo-grow-y", title: (w.id || "") + " · " + num(w.count) + " 篇" });
        sv(b, "--h", Math.max(0.04, num(w.count) / wmax).toFixed(3)); sv(b, "--mo-delay", (i * 40) + "ms");
        return el("div", { class: "hm-wk" }, b);
      });
      leftKids.push(el("div", { class: "hm-wkwrap" }, [
        el("div", { class: "hm-panel-t mono", text: "近 " + weeks.length + " 周入库量" }),
        el("div", { class: "hm-wks" }, cols),
        el("div", { class: "hm-wk-axis mono" }, [el("span", { text: weeks[0].id || "" }), el("span", { text: weeks[weeks.length - 1].id || "" })])
      ]));
    }
    var left = el("div", { class: "hm-panel" }, leftKids);

    // ---------- 右：季度融资 ----------
    var bars = ND && ND.quarterBars ? ND.quarterBars(arr(NEWS.funding_quarters)) : [];
    bars = bars.slice(-MAX_QUARTERS);
    var rightKids = [el("div", { class: "hm-panel-t mono", text: "资本：季度融资合计" })];
    if (!bars.length) {
      rightKids.push(el("div", { class: "hm-hint-box", text: "还没有已披露金额的融资事件，暂无季度汇总。收录到融资新闻后这里会出现柱状图。" }));
    } else {
      var fcols = bars.map(function (b, i) {
        var box = el("div", { class: "dg-fbox", style: "height:" + (num(b.h) * 100).toFixed(1) + "%" }, [
          num(b.h) > 0 ? el("div", { class: "dg-fbar mo-grow-y" }) : el("div", { class: "dg-fbar dg-fbar-z" }),
          el("span", { class: "dg-fval mono", text: ND.fmtUsd(b.total_usd) })
        ]);
        var bar = box.firstChild; sv(bar, "--mo-delay", (i * 90) + "ms");
        return el("div", { class: "dg-fcol" }, [
          el("div", { class: "dg-fplot" }, box),
          el("div", { class: "dg-fq mono", text: str(b.quarter) }),
          el("div", { class: "dg-fn", text: num(b.n_events) + " 笔" })
        ]);
      });
      rightKids.push(el("div", { class: "dg-fchart", role: "img", "aria-label": "季度融资合计柱状图：" + bars.map(function (b) { return b.quarter + " " + ND.fmtUsd(b.total_usd); }).join("，") }, fcols));
    }
    rightKids.push(el("div", { class: "hint dg-fnote", text: "仅统计本库已收录且披露金额的事件，不代表全行业融资总额。" }));
    rightKids.push(more(c, "#/news?view=funding", "融资汇总 →"));
    var right = el("div", { class: "hm-panel" }, rightKids);

    var pgrid = el("div", { class: "dg-pgrid", "data-mo-demo": "" }, [left, right]);
    sv(pgrid, "--mo-duration", ".7s");
    c.rise(pgrid, { start: 0, end: 22 });
    M.watch(pgrid, function () {
      c.play(pgrid);
      if (heatGrid) {
        var t = setTimeout(function () { heatGrid.classList.add("is-done"); }, heatDone);
        M.onCleanup(function () { clearTimeout(t); });
      }
    }, null, { threshold: 0.15, once: true });
    return el("section", { class: "hm-sec dg-pulse", "aria-label": "领域脉搏" }, [
      head(c, "Research × Capital", "领域", "脉搏", el("a", { class: "btn", href: "#/map", text: "进入范式地图 →" })),
      pgrid
    ]);
  }

  window.AIVCDigest = { digest: digest, pulse: pulse };
})();
