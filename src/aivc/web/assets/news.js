/* 商业新闻页：时间线 / 融资汇总 / 公司（含公司档案）。
 * 入口 window.AIVCNews.render(api, route) 返回根元素；所有文本经 api.el 输出。
 * 纯函数（筛选/分组/金额格式化）在 news-data.js。路由：
 *   #/news[?type=&company=&amount=&q=&major=1&unscored=1]   时间线
 *   #/news?view=funding | #/news?view=companies            融资汇总 / 公司列表
 *   #/news/company/<id>                                     公司档案 */
(function () {
  "use strict";

  var ND = window.AIVCNewsData;
  var EVENT_FALLBACK = [
    { id: "funding", label: "融资" }, { id: "partnership", label: "合作" }, { id: "m_and_a", label: "并购" },
    { id: "model_or_data_release", label: "模型或数据发布" }, { id: "clinical", label: "临床进展" },
    { id: "people", label: "重要人事" }, { id: "other", label: "其他" }
  ];
  var FUNDING_NOTE = "仅统计本库已收录且披露金额的事件，不代表公司的实际累计融资。";
  var BATCH = 60;

  function render(api, route) {
    if (!ND) throw new Error("news-data.js 未加载");
    var el = api.el, arr = api.arr, str = api.str, tag = api.tag;
    var D = api.D, M = api.motion;
    var N = D.news || { items: [], count: 0, event_types: [], weeks: [], funding_quarters: [] };
    var C = D.companies || { companies: [], categories: {} };
    var items = arr(N.items);
    var companies = arr(C.companies);
    var companyById = D.companyById || {};
    var itemById = {};
    items.forEach(function (it) { itemById[it.id] = it; });
    var eventTypes = arr(N.event_types).length ? arr(N.event_types) : EVENT_FALLBACK;
    var typeLabel = {};
    eventTypes.forEach(function (t) { typeLabel[t.id] = t.label; });

    route = route || {};
    var parts = arr(route.parts);
    var params = route.params || new URLSearchParams("");
    var view = params.get("view") || "";

    // ---------- 小工具 ----------
    function timer(fn, ms) {
      var t = setTimeout(fn, ms);
      M.onCleanup(function () { clearTimeout(t); });
      return t;
    }
    function usdOrDash(x) { return typeof x === "number" && x > 0 ? ND.fmtUsd(x) : "—"; }
    function nameToCompany(name) {
      var low = str(name).toLowerCase();
      if (!low) return null;
      for (var i = 0; i < companies.length; i++) {
        var c = companies[i];
        var keys = [c.name].concat(arr(c.aliases)).map(function (k) { return str(k).toLowerCase(); }).filter(function (k) { return k.length >= 3; });
        for (var j = 0; j < keys.length; j++) if (low === keys[j] || low.indexOf(keys[j]) >= 0) return c;
      }
      return null;
    }
    function companyChips(it) {
      var used = {}, out = [];
      arr(it.companies).forEach(function (n) {
        var c = nameToCompany(n) || (arr(it.company_ids).length === 1 && arr(it.companies).length === 1 ? companyById[it.company_ids[0]] : null);
        if (c && !used[c.id]) {
          used[c.id] = 1;
          out.push(el("a", { class: "tag tag-task nw-chip", href: "#/news/company/" + encodeURIComponent(c.id), text: str(n), "aria-label": "公司档案：" + str(n) }));
        } else if (!c) {
          out.push(tag(str(n), "tag-par"));
        }
      });
      arr(it.company_ids).forEach(function (id) {
        var c = companyById[id];
        if (c && !used[id]) {
          used[id] = 1;
          out.push(el("a", { class: "tag tag-task nw-chip", href: "#/news/company/" + encodeURIComponent(id), text: str(c.name) }));
        }
      });
      return out;
    }
    function sourceLinks(it) {
      var list = arr(it.sources).filter(function (s) { return s && api.safeUrl(s.url); });
      if (!list.length && api.safeUrl(it.url)) list = [{ name: it.source, url: it.url }];
      var kids = [el("span", { class: "nw-lab", text: "来源：" })];
      list.forEach(function (s, i) {
        if (i) kids.push(el("span", { class: "nw-sep", text: "、" }));
        kids.push(api.extLink(s.url, str(s.name) || str(it.source) || "原文", "nw-src"));
      });
      if (!list.length) kids.push(el("span", { text: str(it.source) || "未知" }));
      return el("div", { class: "nw-sources hint" }, kids);
    }

    // ---------- 单条新闻（行式） ----------
    function itemRow(it) {
      var tagsLine = [];
      tagsLine.push(tag(typeLabel[it.event_type] || str(it.event_type) || "其他", "tag-par"));
      if (it.major) tagsLine.push(tag("重大事件", "tag-hl"));
      if (typeof it.amount_usd === "number") tagsLine.push(tag(ND.fmtUsd(it.amount_usd), "tag-good"));
      if (str(it.round)) tagsLine.push(tag(str(it.round), "tag-good"));
      if (it.scored === false) tagsLine.push(tag("未处理", "tag-warn"));
      var title = api.extLink(it.url, str(it.title) || "(无标题)", "nw-title");
      if (!title) title = el("span", { class: "nw-title", text: str(it.title) || "(无标题)" });
      var chips = companyChips(it);
      var reasons = arr(it.major_reasons);
      return el("article", { class: "nw-item" + (it.major ? " is-major" : "") }, [
        el("div", { class: "nw-date mono", text: str(it.date) }),
        el("div", { class: "nw-body" }, [
          el("div", { class: "tags nw-tags" }, tagsLine),
          el("h3", { class: "nw-h" }, title),
          chips.length ? el("div", { class: "tags nw-chips" }, chips) : null,
          arr(it.investors).length ? el("div", { class: "nw-inv hint", text: "投资方：" + arr(it.investors).join("、") }) : null,
          str(it.summary_zh) ? el("p", { class: "nw-sum", text: it.summary_zh }) : (it.scored === false ? el("p", { class: "nw-sum hint", text: "尚未经 LLM 处理，仅显示标题。" }) : null),
          str(it.significance_zh) ? el("p", { class: "nw-sig" }, [el("span", { class: "nw-lab", text: "要点 " }), str(it.significance_zh)]) : null,
          it.major && reasons.length ? el("p", { class: "nw-reason mono", text: "重大事件判定：" + reasons.join("；") }) : null,
          sourceLinks(it)
        ])
      ]);
    }

    // ---------- 页头与标签 ----------
    function tabs(active) {
      var defs = [["timeline", "时间线", "#/news"], ["funding", "融资汇总", "#/news?view=funding"], ["companies", "公司", "#/news?view=companies"]];
      return el("nav", { class: "nw-tabs", "aria-label": "商业新闻视图" }, defs.map(function (d) {
        var a = el("a", { class: "nw-tab", href: d[2], text: d[1] });
        if (d[0] === active) a.setAttribute("aria-current", "page");
        return a;
      }));
    }
    function pageHead(active) {
      var gen = str(N.generated_at).replace("T", " ");
      return [
        el("div", { class: "eyebrow mono", text: "Business News" }),
        el("h1", { class: "page-title" }, ["商业", el("span", { class: "hl", text: "新闻" })]),
        el("p", { class: "lede", text: "追踪虚拟细胞方向（细胞基础模型、扰动数据、单细胞与多组学 AI 平台）的融资、合作、并购、模型与数据发布和重要人事；只展示相关度不低于 6 的条目，一般的 AI 药物发现新闻不在此列。" }),
        el("p", { class: "hint nw-meta mono" }, "展示 " + ND.filterNews(items, {}).length + " 条" + (items.length > ND.filterNews(items, {}).length ? "（另有 " + (items.length - ND.filterNews(items, {}).length) + " 条未打分，默认隐藏）" : "") + (gen ? " · 更新于 " + gen : "")),
        el("div", { class: "notice" }, "摘要由 LLM 依据公开标题与摘要片段生成，可能有误，请以原文为准。每条均链接回原文并注明来源。"),
        tabs(active)
      ];
    }
    function emptyData() {
      return el("div", { class: "empty" }, [
        el("p", { text: "还没有新闻数据。" }),
        el("p", { class: "hint" }, ["运行 ", el("code", { text: "uv run python -m aivc news" }), " 抓取并处理，再 ", el("code", { text: "uv run python -m aivc build-site" }), " 刷新本站。"])
      ]);
    }

    // ---------- 视图 a：时间线 ----------
    function viewTimeline() {
      var state = {
        type: params.get("type") || "", company: params.get("company") || "", amount: params.get("amount") || "",
        q: params.get("q") || "", major: params.get("major") === "1", unscored: params.get("unscored") === "1"
      };
      var limit = BATCH;
      var results = el("div", { class: "nw-results", "aria-live": "polite" });
      var countEl = el("span", { class: "count" });
      var clearBtn = el("button", { type: "button", class: "btn btn-ghost nw-clear", text: "清除筛选" });

      function writeHash() {
        var p = new URLSearchParams();
        if (state.type) p.set("type", state.type);
        if (state.company) p.set("company", state.company);
        if (state.amount) p.set("amount", state.amount);
        if (state.q.trim()) p.set("q", state.q.trim());
        if (state.major) p.set("major", "1");
        if (state.unscored) p.set("unscored", "1");
        var qs = p.toString();
        try { history.replaceState(null, "", "#/news" + (qs ? "?" + qs : "")); } catch (e) { /* ignore */ }
      }
      function hasFilter() { return !!(state.type || state.company || state.amount || state.q.trim() || state.major); }

      function draw() {
        api.clear(results);
        var list = ND.filterNews(items, { type: state.type, company: state.company, amount: state.amount, q: state.q, major: state.major, includeUnscored: state.unscored });
        var total = ND.filterNews(items, { includeUnscored: state.unscored }).length;
        countEl.textContent = "匹配 " + list.length + " / " + total + " 条";
        clearBtn.hidden = !hasFilter();
        if (!items.length) { results.appendChild(emptyData()); return; }
        if (!list.length) {
          results.appendChild(el("div", { class: "empty" }, [
            el("p", { text: "没有符合当前筛选条件的新闻。" }),
            el("button", { type: "button", class: "btn", text: "清除筛选", onclick: reset })
          ]));
          return;
        }
        var groups = ND.groupByWeek(list), shown = 0, more = false;
        groups.forEach(function (g) {
          if (shown >= limit) { more = true; return; }
          var take = g.items.slice(0, limit - shown);
          if (take.length < g.items.length) more = true;
          shown += take.length;
          results.appendChild(el("section", { class: "nw-week" }, [
            el("header", { class: "nw-weekhead" }, [
              el("h2", { class: "nw-weekid mono", text: g.id }),
              el("span", { class: "nw-weekrange hint", text: g.start ? g.start + " ~ " + g.end : "" }),
              el("span", { class: "nw-weekn count", text: g.items.length + " 条" })
            ]),
            el("div", { class: "nw-list" }, take.map(itemRow))
          ]));
        });
        if (more) results.appendChild(el("div", { class: "more" }, el("button", { type: "button", class: "btn", text: "显示更多", onclick: function () { limit += BATCH; draw(); } })));
        api.reveal(results);
      }
      function change() { limit = BATCH; writeHash(); draw(); syncChips(); }
      function reset() {
        state.type = ""; state.company = ""; state.amount = ""; state.q = ""; state.major = false;
        search.value = ""; companySel.value = ""; amountSel.value = ""; majorChk.checked = false;
        change();
      }
      clearBtn.addEventListener("click", reset);

      var search = el("input", { type: "search", class: "search", id: "nw-q", placeholder: "搜索标题、摘要、公司、投资方、来源…", "aria-label": "搜索新闻", value: state.q, autocomplete: "off" });
      var searchTimer = 0;
      search.addEventListener("input", function () {
        clearTimeout(searchTimer);
        searchTimer = timer(function () { state.q = search.value; change(); }, 180);
      });

      var chipBtns = [];
      function typeChip(id, label) {
        var b = el("button", { type: "button", class: "nw-chipbtn", text: label, "aria-pressed": "false", "data-type": id });
        b.addEventListener("click", function () { state.type = id; change(); });
        chipBtns.push(b);
        return b;
      }
      function syncChips() { chipBtns.forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-type") === state.type ? "true" : "false"); }); }
      var chipRow = el("div", { class: "nw-chiprow", role: "group", "aria-label": "事件类型" },
        [typeChip("", "全部")].concat(eventTypes.map(function (t) { return typeChip(t.id, t.label); })));

      var withNews = companies.filter(function (c) { return c.n_news > 0; });
      var companySel = el("select", { id: "nw-company", "aria-label": "按公司筛选" }, [el("option", { value: "", text: "全部公司" })].concat(withNews.map(function (c) {
        return el("option", { value: c.id, text: str(c.name) + "（" + c.n_news + "）" });
      })));
      if (state.company && !withNews.some(function (c) { return c.id === state.company; })) {
        var extra = companyById[state.company];
        companySel.appendChild(el("option", { value: state.company, text: extra ? str(extra.name) : state.company }));
      }
      companySel.value = state.company;
      companySel.addEventListener("change", function () { state.company = companySel.value; change(); });

      var amountSel = el("select", { id: "nw-amount", "aria-label": "按金额档位筛选" }, [el("option", { value: "", text: "全部金额" })].concat(ND.AMOUNT_BUCKETS.map(function (b) {
        return el("option", { value: b.id, text: b.label });
      })));
      amountSel.value = state.amount;
      amountSel.addEventListener("change", function () { state.amount = amountSel.value; change(); });

      function toggle(label, key, init, aria) {
        var inp = el("input", { type: "checkbox", "aria-label": aria });
        inp.checked = init;
        inp.addEventListener("change", function () { state[key] = inp.checked; change(); });
        return { box: el("label", { class: "chk" }, [inp, label]), input: inp };
      }
      var majorT = toggle("只看重大", "major", state.major, "只看重大事件");
      var majorChk = majorT.input;
      var unscT = toggle("含未处理", "unscored", state.unscored, "包含尚未经 LLM 处理的条目");

      var bar = el("div", { class: "card filters nw-filters" }, [
        search,
        chipRow,
        el("div", { class: "nw-fline" }, [
          el("div", { class: "field" }, [el("label", { for: "nw-company", text: "公司" }), companySel]),
          el("div", { class: "field" }, [el("label", { for: "nw-amount", text: "金额" }), amountSel]),
          el("div", { class: "checks nw-checks" }, [majorT.box, unscT.box])
        ]),
        el("div", { class: "fbar" }, [countEl, clearBtn])
      ]);
      syncChips();
      draw();
      return [bar, results];
    }

    // ---------- 视图 b：融资汇总 ----------
    function viewFunding() {
      var bars = ND.quarterBars(arr(N.funding_quarters));
      var note = el("p", { class: "hint nw-fnote", text: FUNDING_NOTE });
      var chart;
      if (!bars.length) {
        chart = el("div", { class: "empty" }, "还没有已披露金额的融资事件，暂无季度汇总。");
      } else {
        var cols = bars.map(function (b, i) {
          var box = el("div", { class: "nw-barbox", style: "height:" + (b.h * 100).toFixed(1) + "%" }, [
            b.h > 0 ? el("div", { class: "nw-bar mo-grow-y", style: "--mo-delay:" + (i * 90) + "ms; --mo-duration:.8s" }) : el("div", { class: "nw-bar nw-bar-z" }),
            el("span", { class: "nw-bval mono", text: ND.fmtUsd(b.total_usd) })
          ]);
          return el("div", { class: "nw-col" }, [
            el("div", { class: "nw-plot" }, box),
            el("div", { class: "nw-qlabel mono", text: str(b.quarter) }),
            el("div", { class: "nw-qmeta hint", text: (b.n_events || 0) + " 笔" + (b.n_undisclosed ? " · 未披露 " + b.n_undisclosed : "") })
          ]);
        });
        var plot = el("div", { class: "nw-chart", "data-mo-demo": "", role: "img", "aria-label": "季度融资合计柱状图：" + bars.map(function (b) { return b.quarter + " " + ND.fmtUsd(b.total_usd); }).join("，") }, cols);
        chart = el("div", { class: "card nw-chartcard" }, [plot]);
        M.watch(plot, function (n) { M.play(n); }, null, { once: true, threshold: 0.2 });
        if (M.reduced) M.play(plot);
      }
      var tops = bars.filter(function (b) { return arr(b.top).length; }).map(function (b) {
        return el("div", { class: "card nw-topcard" }, [
          el("div", { class: "eyebrow mono", text: str(b.quarter) + " · 金额前 " + Math.min(3, arr(b.top).length) }),
          el("ol", { class: "nw-toplist" }, arr(b.top).slice(0, 3).map(function (t) {
            var full = itemById[t.id];
            var label = arr(t.companies).join("、") || str(t.title);
            var link = full && api.safeUrl(full.url) ? api.extLink(full.url, str(t.title) || label, "nw-src") : el("span", { text: str(t.title) || label });
            return el("li", {}, [el("b", { class: "mono", text: ND.fmtUsd(t.amount_usd) }), " ", el("span", { class: "hint", text: label ? label + " · " : "" }), link]);
          }))
        ]);
      });
      var fund = items.filter(function (it) { return it.event_type === "funding"; }).sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : 0; });
      var table = fund.length ? el("div", { class: "card tablewrap" }, el("table", { class: "data nw-table" }, [
        el("thead", {}, el("tr", {}, ["日期", "公司", "轮次", "金额", "投资方", "来源"].map(function (h) { return el("th", { text: h }); }))),
        el("tbody", {}, fund.map(function (it) {
          return el("tr", {}, [
            el("td", { class: "mono", text: str(it.date) }),
            el("td", {}, companyChips(it).length ? el("div", { class: "tags" }, companyChips(it)) : el("span", { class: "hint", text: "–" })),
            el("td", { text: str(it.round) || "–" }),
            el("td", { class: "mono", text: ND.fmtUsd(it.amount_usd) }),
            el("td", { text: arr(it.investors).join("、") || "–" }),
            el("td", {}, sourceLinks(it))
          ]);
        }))
      ])) : el("div", { class: "empty" }, "库内暂无融资事件。");
      return [
        el("section", { class: "section nw-sec" }, [el("div", { class: "section-head" }, el("h2", { text: "季度融资" })), note, chart]),
        tops.length ? el("section", { class: "section nw-sec" }, [el("div", { class: "section-head" }, el("h2", { text: "各季度金额前三" })), el("div", { class: "nw-topgrid" }, tops)]) : null,
        el("section", { class: "section nw-sec" }, [el("div", { class: "section-head" }, el("h2", { text: "融资事件" })), table])
      ];
    }

    // ---------- 视图 c：公司列表与档案 ----------
    function viewCompanies() {
      if (!companies.length) return el("div", { class: "empty", text: "公司名单为空。" });
      var cats = C.categories || {};
      var order = Object.keys(cats);
      companies.forEach(function (c) { if (order.indexOf(c.category) < 0) order.push(c.category); });
      var blocks = order.map(function (cat) {
        var list = companies.filter(function (c) { return c.category === cat; }).sort(function (a, b) {
          return (b.n_news - a.n_news) || (b.n_papers - a.n_papers) || (str(a.name) < str(b.name) ? -1 : 1);
        });
        if (!list.length) return null;
        return el("section", { class: "section nw-sec" }, [
          el("div", { class: "section-head" }, el("h2", { text: cats[cat] || cat || "其他" })),
          el("div", { class: "card tablewrap" }, el("table", { class: "data nw-table" }, [
            el("thead", {}, el("tr", {}, ["公司", "新闻", "库内融资", "最近事件", "相关论文"].map(function (h) { return el("th", { text: h }); }))),
            el("tbody", {}, list.map(function (c) {
              return el("tr", {}, [
                el("td", {}, el("a", { href: "#/news/company/" + encodeURIComponent(c.id), text: str(c.name) })),
                el("td", { class: "mono", text: String(c.n_news || 0) }),
                el("td", { class: "mono", text: usdOrDash(c.funding_total_usd) }),
                el("td", { class: "mono", text: str(c.last_event_date) || "—" }),
                el("td", { class: "mono", text: String(c.n_papers || 0) })
              ]);
            }))
          ]))
        ]);
      });
      return [el("p", { class: "hint nw-fnote", text: "库内融资：" + FUNDING_NOTE }), blocks];
    }

    function stat(label, value) {
      return el("div", { class: "nw-stat" }, [el("div", { class: "nw-statv mono", text: String(value) }), el("div", { class: "nw-statl hint", text: label })]);
    }
    function viewCompany(id) {
      var c = companyById[id];
      var back = el("a", { class: "btn btn-ghost nw-back", href: "#/news?view=companies", text: "← 全部公司" });
      if (!c) {
        return el("div", { class: "wrap page nw-page" }, [
          el("div", { class: "eyebrow mono", text: "Company" }),
          el("div", { class: "card empty" }, [el("p", { text: "未找到公司：" + id }), back])
        ]);
      }
      var cats = C.categories || {};
      var relNews = arr(c.news_ids).map(function (i) { return itemById[i]; }).filter(Boolean);
      relNews.sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : 0; });
      var papers = arr(c.paper_ids).map(function (i) { return api.byId[i]; }).filter(Boolean);
      var paperLimit = 15;
      var paperBox = el("div", { class: "nw-papers" });
      function drawPapers() {
        api.clear(paperBox);
        papers.slice(0, paperLimit).forEach(function (p) {
          var b = el("button", { type: "button", class: "nw-prow", "aria-haspopup": "dialog" }, [
            el("span", { class: "nw-pdate mono", text: str(p.date) }),
            el("span", { class: "nw-ptitle", text: str(p.title) || "(无标题)" }),
            el("span", { class: "nw-pscore mono", text: p.scored && typeof p.final_score === "number" ? api.fmtScore(p.final_score) : "" })
          ]);
          b.addEventListener("click", function () { api.openDrawer(p, b); });
          paperBox.appendChild(b);
        });
        if (papers.length > paperLimit) paperBox.appendChild(el("div", { class: "more" }, el("button", { type: "button", class: "btn", text: "显示更多（还有 " + (papers.length - paperLimit) + " 篇）", onclick: function () { paperLimit += 30; drawPapers(); } })));
      }
      drawPapers();
      var fe = arr(c.funding_events);
      var fundTable = fe.length ? el("div", { class: "card tablewrap" }, el("table", { class: "data nw-table" }, [
        el("thead", {}, el("tr", {}, ["日期", "轮次", "金额"].map(function (h) { return el("th", { text: h }); }))),
        el("tbody", {}, fe.map(function (f) {
          return el("tr", {}, [el("td", { class: "mono", text: str(f.date) }), el("td", { text: str(f.round) || "–" }), el("td", { class: "mono", text: ND.fmtUsd(f.amount_usd) })]);
        }))
      ])) : el("p", { class: "hint", text: "库内暂无该公司的融资事件。" });
      return el("div", { class: "wrap page nw-page" }, [
        el("div", { class: "eyebrow mono", text: "Company · " + (cats[c.category] || c.category || "") }),
        el("h1", { class: "page-title nw-ctitle", text: str(c.name) }),
        el("div", { class: "tags nw-ctags" }, [
          tag(cats[c.category] || str(c.category) || "其他", "tag-par"),
          c.tier === "top" ? tag("重点关注", "tag-hl") : null
        ].concat(arr(c.aliases).map(function (a) { return tag("别名 " + str(a)); }))),
        el("div", { class: "links nw-clinks" }, [api.extLink(c.url, "官网 ↗", "btn btn-primary"), back]),
        arr(c.models).length ? el("div", { class: "nw-models" }, [el("h2", { class: "nw-subh", text: "已知模型 / 数据集" }), el("div", { class: "tags" }, arr(c.models).map(function (m) { return tag(str(m), "tag-task"); }))]) : null,
        el("div", { class: "nw-stats card" }, [stat("相关新闻", c.n_news || 0), stat("库内融资合计", usdOrDash(c.funding_total_usd)), stat("相关论文", c.n_papers || 0), stat("最近事件", str(c.last_event_date) || "—")]),
        el("p", { class: "hint nw-fnote", text: "库内融资合计：" + FUNDING_NOTE }),
        el("section", { class: "section nw-sec" }, [el("div", { class: "section-head" }, el("h2", { text: "融资事件" })), fundTable]),
        el("section", { class: "section nw-sec" }, [
          el("div", { class: "section-head" }, el("h2", { text: "相关新闻" })),
          relNews.length ? el("div", { class: "nw-list" }, relNews.map(itemRow)) : el("div", { class: "empty", text: "库内暂无该公司的新闻。" })
        ]),
        el("section", { class: "section nw-sec" }, [
          el("div", { class: "section-head" }, el("h2", { text: "相关论文" })),
          el("p", { class: "hint", text: "论文标题或摘要中出现该公司名称、或其模型 / 数据集名称（词边界匹配）。" }),
          papers.length ? paperBox : el("div", { class: "empty", text: "论文库中暂无提到该公司或其模型的论文。" })
        ])
      ]);
    }

    // ---------- 组装 ----------
    if (parts[1] === "company") return viewCompany(parts[2] ? String(parts[2]) : "");
    var active = view === "funding" ? "funding" : view === "companies" ? "companies" : "timeline";
    var body = active === "funding" ? viewFunding() : active === "companies" ? viewCompanies() : viewTimeline();
    return el("div", { class: "wrap page nw-page" }, [pageHead(active), el("div", { class: "nw-body-wrap" }, body)]);
  }

  window.AIVCNews = { render: render };
})();
