/* 知识资源页：按类型分组的精选目录（仓库 / 数据门户 / 基准 / 工具 / 学习资源）。
 * 入口 window.AIVCResources.render(api, route) 返回根元素；所有文本经 api.el 输出，不用 innerHTML。
 * 纯函数（筛选/分组/star 格式化/索引）在 resources-data.js。路由：
 *   #/resources[?kind=&task=&paradigm=&maintained=yes|no&stars=ge5k|1k_5k|100_1k|lt100&q=]
 *   #/resources?id=<资源 id>   定位并高亮某条 */
(function () {
  "use strict";

  var RD = window.AIVCResourcesData;
  var MAINT_OPTS = [["yes", "维护中"], ["no", "已归档 / 久未更新"]];

  function render(api, route) {
    if (!RD) throw new Error("resources-data.js 未加载");
    var el = api.el, arr = api.arr, str = api.str, M = api.motion;
    var R = api.D.resources || {};
    var items = arr(R.items);
    var kinds = arr(R.kinds);
    var sections = arr(R.sections);
    var params = (route && route.params) || new URLSearchParams("");
    var page = el("div", { class: "wrap page rs-page" });

    var days = typeof R.maintained_within_days === "number" ? R.maintained_within_days : 365;
    var fetched = str(R.metrics_fetched_at).replace("T", " ").replace(/:\d\dZ?$/, "").replace(/Z$/, "");
    page.appendChild(el("div", { class: "eyebrow mono", text: "Knowledge Resources" }));
    page.appendChild(el("h1", { class: "page-title" }, ["知识", el("span", { class: "hl", text: "资源" })]));
    page.appendChild(el("p", { class: "lede", text: "虚拟细胞与扰动建模方向的代表性实验室、仓库、数据集与基准、综述博客，以及社区维护的资源合集。以精选为主、不求全，每月人工维护一次。" }));
    page.appendChild(el("p", { class: "hint rs-meta mono" },
      "收录 " + (R.count || items.length) + " 条" + (fetched ? " · GitHub 指标抓取于 " + fetched + " UTC" : " · 尚未抓取 GitHub 指标")));
    page.appendChild(el("div", { class: "notice" }, "星标数与最近推送时间来自 GitHub 公开接口，仅供参考；「维护中」表示最近 " + days + " 天内有推送，不代表质量。描述为本站编写的简介，请以各资源官方页面为准。"));

    if (!items.length) {
      page.appendChild(el("div", { class: "empty" }, [
        el("p", { text: "还没有资源数据。" }),
        el("p", { class: "hint" }, ["运行 ", el("code", { text: "uv run python -m aivc build-site" }), " 生成 data/resources.json 后刷新本页。"])
      ]));
      return page;
    }

    var tasks = arr(R.tasks), pars = arr(R.paradigms);
    var kindLabel = {}, taskLabel = {}, parLabel = {};
    kinds.forEach(function (k) { kindLabel[k.id] = k.label; });
    tasks.forEach(function (t) { taskLabel[t.id] = t.label; });
    pars.forEach(function (t) { parLabel[t.id] = t.label; });
    var byId = {};
    items.forEach(function (it) { byId[it.id] = it; });

    var state = {
      kind: params.get("kind") || "", task: params.get("task") || "", paradigm: params.get("paradigm") || "",
      maintained: params.get("maintained") || "", stars: params.get("stars") || "", q: params.get("q") || ""
    };
    var targetId = params.get("id") || "";
    var results = el("div", { class: "rs-results", "aria-live": "polite" });
    var countEl = el("span", { class: "count" });
    var clearBtn = el("button", { type: "button", class: "btn btn-ghost rs-clear", text: "清除筛选" });
    var inputs = {};

    function writeHash() {
      var p = new URLSearchParams();
      ["kind", "task", "paradigm", "maintained", "stars"].forEach(function (k) { if (state[k]) p.set(k, state[k]); });
      if (state.q.trim()) p.set("q", state.q.trim());
      var qs = p.toString();
      try { history.replaceState(null, "", "#/resources" + (qs ? "?" + qs : "")); } catch (e) { /* ignore */ }
    }
    function hasFilter() { return !!(state.kind || state.task || state.paradigm || state.maintained || state.stars || state.q.trim()); }

    function statusBadge(g) {
      if (g.archived === true) return el("span", { class: "tag tag-warn", text: "已归档" });
      if (g.maintained === true) return el("span", { class: "tag tag-good", text: "维护中" });
      if (g.maintained === false) return el("span", { class: "tag tag-warn", text: "久未更新" });
      return null;
    }
    function ghLine(g) {
      var bits = [];
      if (typeof g.stars === "number") {
        var d = typeof g.stars_delta === "number" && g.stars_delta !== 0 ? " (" + (g.stars_delta > 0 ? "+" : "") + g.stars_delta + ")" : "";
        bits.push(el("span", { class: "rs-star", title: g.stars + " stars", text: "★ " + RD.fmtStars(g.stars) + d }));
      }
      if (str(g.pushed_at)) bits.push(el("span", { text: "推送 " + str(g.pushed_at).slice(0, 10) }));
      if (str(g.language)) bits.push(el("span", { text: str(g.language) }));
      if (str(g.license)) bits.push(el("span", { text: str(g.license) }));
      var badge = statusBadge(g);
      if (badge) bits.push(badge);
      return bits.length ? el("div", { class: "rs-gh mono" }, bits) : null;
    }
    function filterTag(text, cls, key, val) {
      return el("a", { class: "tag " + cls + " rs-tagl", href: "#/papers?" + key + "=" + encodeURIComponent(val), title: "在论文流中查看该" + (key === "task" ? "任务" : "范式"), text: text });
    }
    function relatedBlock(it) {
      var parts = [];
      var papers = arr(it.paper_ids).map(function (id) { return api.byId[id]; }).filter(Boolean);
      if (papers.length) {
        parts.push(el("div", { class: "rs-rel" }, [el("span", { class: "rs-rel-l", text: "关联论文" })].concat(papers.slice(0, 4).map(function (p) {
          var b = el("button", { type: "button", class: "linkbtn", "aria-haspopup": "dialog", text: str(p.title) || str(p.id) });
          b.addEventListener("click", function () { api.openDrawer(p, b); });
          return b;
        }))));
      }
      var dn = arr(it.dataset_names).map(str).filter(Boolean);
      if (dn.length) {
        parts.push(el("div", { class: "rs-rel" }, [el("span", { class: "rs-rel-l", text: "论文流中检索" })].concat(dn.slice(0, 6).map(function (n) {
          return el("a", { class: "linkbtn", href: "#/papers?q=" + encodeURIComponent(n), text: n });
        }))));
      }
      return parts;
    }
    function card(it) {
      var g = it.github && typeof it.github === "object" ? it.github : null;
      var link = api.extLink(it.url, str(it.name) + " ↗", "rs-name");
      var tags = [];
      arr(it.related_tasks).forEach(function (t) { tags.push(filterTag(taskLabel[t] || t, "tag-task", "task", t)); });
      arr(it.related_paradigms).forEach(function (t) { tags.push(filterTag(parLabel[t] || t, "tag-par", "paradigm", t)); });
      arr(it.tags).forEach(function (t) { tags.push(api.tag(str(t))); });
      var node = el("article", { class: "card rs-card reveal", id: "rs-" + str(it.id), "data-rs-id": str(it.id) }, [
        el("div", { class: "rs-head" }, [
          el("h3", { class: "rs-h" }, link || el("span", { class: "rs-name", text: str(it.name) })),
          el("span", { class: "tag rs-kind", text: kindLabel[it.kind] || str(it.kind) })
        ]),
        g && str(g.full_name) ? el("div", { class: "rs-full mono", text: str(g.full_name) }) : null,
        el("p", { class: "rs-desc", text: str(it.description_zh) }),
        g ? ghLine(g) : null,
        tags.length ? el("div", { class: "tags rs-tags" }, tags) : null,
        relatedBlock(it)
      ]);
      if (targetId && it.id === targetId) node.classList.add("is-target");
      return node;
    }

    function draw() {
      api.clear(results);
      var list = RD.filterResources(items, state);
      countEl.textContent = "匹配 " + list.length + " / " + items.length + " 条";
      clearBtn.hidden = !hasFilter();
      if (targetId && !byId[targetId]) results.appendChild(el("div", { class: "notice", text: "没有找到资源 " + targetId + "，已显示全部精选。" }));
      if (!list.length) {
        results.appendChild(el("div", { class: "empty" }, [
          el("p", { text: "没有符合当前筛选条件的资源。" }),
          el("p", { class: "hint", text: "星标与维护状态只对带 GitHub 指标的仓库生效，其余资源在这两项筛选下不会出现。" }),
          el("button", { type: "button", class: "btn", text: "清除筛选", onclick: clearAll })
        ]));
        return;
      }
      var groups = sections.length ? RD.groupBySection(list, sections, kinds) : RD.groupByKind(list, kinds);
      if (groups.length > 1) {
        results.appendChild(el("nav", { class: "rs-jump", "aria-label": "资源板块" }, groups.map(function (g) {
          return el("button", { type: "button", class: "chip rs-jump-b", text: str(g.label) + " " + g.items.length, onclick: function () {
            var t = results.querySelector('[data-rs-sec="' + str(g.id).replace(/["\\]/g, "") + '"]');
            if (t) { try { t.scrollIntoView({ block: "start", behavior: M.reduced ? "auto" : "smooth" }); } catch (e) { /* ignore */ } }
          } });
        })));
      }
      groups.forEach(function (g) {
        var head = el("h2", { class: "rs-gh2" }, [str(g.label), el("span", { class: "rs-gn mono", text: g.items.length + " 条" })]);
        var grid = el("div", { class: "rs-grid" }, g.items.map(card));
        results.appendChild(el("section", { class: "rs-group", "data-rs-sec": str(g.id), "aria-label": str(g.label) }, [head, grid]));
      });
      api.reveal(results);
      if (targetId && byId[targetId]) {
        var t = results.querySelector('[data-rs-id="' + targetId.replace(/["\\]/g, "") + '"]');
        if (t) {
          var tm = setTimeout(function () { try { t.scrollIntoView({ block: "center", behavior: M.reduced ? "auto" : "smooth" }); } catch (e) { /* ignore */ } }, 60);
          M.onCleanup(function () { clearTimeout(tm); });
        }
        targetId = ""; // 只在首次绘制时定位，之后改筛选不再抢滚动
      }
    }
    function change(key, val) {
      state[key] = val;
      targetId = "";
      writeHash();
      draw();
    }
    function clearAll() {
      ["kind", "task", "paradigm", "maintained", "stars", "q"].forEach(function (k) { state[k] = ""; });
      Object.keys(inputs).forEach(function (k) { inputs[k].value = ""; });
      targetId = "";
      writeHash();
      draw();
    }

    function select(key, label, options) {
      var cur = state[key];
      var known = options.some(function (o) { return o[0] === cur; });
      var opts = [el("option", { value: "", text: "全部" })].concat(options.map(function (o) { return el("option", { value: o[0], text: o[1] }); }));
      if (cur && !known) opts.push(el("option", { value: cur, text: cur }));
      var sel = el("select", { id: "rs-f-" + key }, opts);
      sel.value = cur;
      sel.addEventListener("change", function () { change(key, sel.value); });
      inputs[key] = sel;
      return el("div", { class: "field" }, [el("label", { "for": "rs-f-" + key, text: label }), sel]);
    }

    var search = el("input", { type: "search", class: "search", id: "rs-f-q", placeholder: "搜索名称 / 简介 / 标签 / 仓库名…", "aria-label": "搜索知识资源", autocomplete: "off" });
    search.value = state.q;
    var timer = 0;
    search.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { change("q", search.value); }, 120);
    });
    M.onCleanup(function () { clearTimeout(timer); });
    inputs.q = search;
    clearBtn.addEventListener("click", clearAll);

    var kindOpts = kinds.filter(function (k) { return k.count > 0; }).map(function (k) { return [k.id, k.label + "（" + k.count + "）"]; });
    page.appendChild(el("div", { class: "card filters rs-filters", role: "search", "aria-label": "筛选知识资源" }, [
      search,
      el("div", { class: "rs-fgrid" }, [
        select("kind", "类型", kindOpts),
        select("task", "任务", tasks.map(function (t) { return [t.id, str(t.label)]; })),
        select("paradigm", "范式", pars.map(function (t) { return [t.id, str(t.label)]; })),
        select("maintained", "维护状态", MAINT_OPTS),
        select("stars", "Star 区间", RD.STAR_BUCKETS.map(function (b) { return [b.id, b.label]; }))
      ]),
      el("div", { class: "fbar" }, [countEl, clearBtn])
    ]));
    page.appendChild(results);
    draw();
    return page;
  }

  window.AIVCResources = { render: render };
})();
