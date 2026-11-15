/* AIVC 首页「一篇论文的旅程」：吸附式分步区块。仅暴露 window.AIVCSteps.section(api)。
 * 宽屏：外层滚动容器 + 内层 sticky 面板，按滚动进度阈值切换当前步（每步 0.7 个视口高）。
 * 窄屏 / reduced-motion：不吸附，5 步纵向堆叠并显示终态。文本一律经 api.el 的 text 输出。 */
(function () {
  "use strict";

  var STEPS = [
    { t: "抓取", d: "每日从 arXiv、bioRxiv、OpenReview 等来源拉取新条目。" },
    { t: "关键词预筛", d: "按课题关键词命中打出关键词分，并划分核心圈 / 外围圈 / 范围外。" },
    { t: "去重", d: "以来源前缀加原始编号作为去重键，已在库的条目不会重复入库。" },
    { t: "LLM 打分", d: "大模型给出最终分，并归类到任务与范式，必读论文被单独标出。" },
    { t: "进入周报与地图", d: "打过分的论文汇入当周周报，并落到范式地图的对应格子里。" }
  ];
  var WIDE_Q = "(min-width: 1024px) and (min-height: 560px)";
  var STEP_VH = 0.7; // 每步滚动距离 = 0.7 个视口高（实测）

  function section(api) {
    try { return build(api); } catch (e) {
      try { document.documentElement.dataset.jsError = "steps: " + (e && e.message); } catch (e2) { /* ignore */ }
      return null;
    }
  }

  function pickPaper(api) {
    var ids = (api.D.home.demo && api.arr(api.D.home.demo.pipeline_ids)) || [];
    var first = null;
    for (var i = 0; i < ids.length; i++) {
      var p = api.byId[ids[i]];
      if (!p) continue;
      if (!first) first = p;
      if (p.scored) return p;
    }
    return first;
  }

  function build(api) {
    if (!api || !api.D || !api.D.home) return null;
    var p = pickPaper(api);
    if (!p) return null;
    var el = api.el, str = api.str, arr = api.arr, M = api.motion;
    var reduced = !!M.reduced;
    var wq = null;
    try { wq = window.matchMedia(WIDE_Q); } catch (e) { /* ignore */ }

    var kw = arr(p.matched_keywords).slice(0, 5);
    var kwScore = typeof p.keyword_score === "number" ? p.keyword_score : null;
    var idStr = str(p.id), colon = idStr.indexOf(":");
    var idPrefix = colon > 0 ? idStr.slice(0, colon) : (str(p.source) || "未知");
    var idRest = colon > 0 ? idStr.slice(colon + 1) : idStr;
    var others = Math.max(0, arr(api.D.papers).length - 1);
    var wk = api.D.home.weekly || null;

    function field(label, valueNode, delay) {
      return el("div", { class: "st-field mo-reveal", style: "--mo-delay:" + delay + "ms" }, [
        el("span", { class: "st-flabel", text: label }), valueNode
      ]);
    }
    function badge(text, cls, delay) {
      return el("span", { class: "st-badge mo-pop" + (cls ? " " + cls : ""), style: "--mo-delay:" + delay + "ms", text: text });
    }
    function head(shown) {
      return el("div", { class: "st-head" }, [
        el("div", { class: "st-meta" }, [
          el("span", { text: str(p.source) || "未知来源" }),
          el("span", { text: str(p.date) || "日期未知" }),
          shown ? el("span", { text: api.circleLabel(p.circle) }) : null
        ]),
        el("div", { class: "st-ptitle", text: str(p.title) || "(无标题)" })
      ]);
    }

    function stage(i) {
      var body = [];
      if (i === 0) {
        body.push(field("来源", el("span", { class: "st-val", text: str(p.source) || "未知" }), 80));
        body.push(field("发布日期", el("span", { class: "st-val", text: str(p.date) || "未知" }), 160));
        var ab = str(p.abstract);
        if (ab) body.push(field("摘要", el("span", { class: "st-val st-clip", text: ab.length > 150 ? ab.slice(0, 150) + "…" : ab }), 240));
        body.push(el("div", { class: "st-badges" }, [badge("已抓取", "", 320)]));
      } else if (i === 1) {
        body.push(field("命中关键词", el("span", { class: "st-chips" }, kw.length ? kw.map(function (k, j) {
          return el("span", { class: "st-chip mo-pop", style: "--mo-delay:" + (120 + j * 80) + "ms", text: k });
        }) : el("span", { class: "st-val", text: "（无命中词）" })), 80));
        if (kwScore !== null) {
          body.push(field("关键词分 " + api.fmtScore(kwScore), el("span", { class: "st-track" },
            el("span", { class: "st-fill mo-bar", style: "width:" + Math.max(0, Math.min(100, kwScore * 10)) + "%;--mo-delay:300ms" })), 240));
        }
        body.push(el("div", { class: "st-badges" }, [badge(api.circleLabel(p.circle), "st-b-accent", 560)]));
      } else if (i === 2) {
        body.push(field("去重键", el("span", { class: "st-val st-mono" }, [
          el("span", { class: "st-b-key", text: idPrefix }), ":" + idRest]), 80));
        body.push(field("比对范围", el("span", { class: "st-val", text: "库内其他 " + others + " 篇" }), 200));
        body.push(el("div", { class: "st-badges" }, [badge("未发现重复", "st-b-good", 380)]));
      } else if (i === 3) {
        if (p.scored) {
          body.push(el("div", { class: "st-score mo-pop", style: "--mo-delay:80ms" }, [
            el("span", { class: "st-score-num", text: api.fmtScore(p.final_score) }),
            el("span", { class: "st-score-un", text: "/ 10" })
          ]));
          var tags = arr(p.tasks).slice(0, 3).map(function (t) { return api.tag(api.taskLabel(t), "tag-task"); })
            .concat(arr(p.paradigms).slice(0, 3).map(function (t) { return api.tag(api.parLabel(t), "tag-par"); }));
          if (tags.length) body.push(field("任务与范式", el("span", { class: "st-chips" }, tags), 200));
          body.push(el("div", { class: "st-badges" }, [badge(p.must_read ? "必读" : "非必读", p.must_read ? "st-b-accent" : "", 360)]));
        } else {
          body.push(el("div", { class: "st-badges" }, [badge("待打分", "st-b-warn", 120)]));
          body.push(field("说明", el("span", { class: "st-val", text: "该论文尚未经 LLM 打分，暂无最终分与标签。" }), 240));
        }
      } else {
        if (wk && wk.id) {
          body.push(field("收录周报", el("a", { class: "st-link", href: "#/weekly/" + encodeURIComponent(wk.id), text: str(wk.title) || str(wk.id) }), 80));
        }
        body.push(field("范式地图", el("a", { class: "st-link", href: "#/map", text: "在范式地图中查看分布 →" }), 200));
        body.push(el("div", { class: "st-badges" }, [badge("已收录", "st-b-good", 360)]));
      }
      return el("div", { class: "st-stage", "data-mo-demo": "", "data-i": String(i), "aria-hidden": "true" },
        el("div", { class: "st-card" }, [
          el("div", { class: "st-card-step mono", text: "步骤 " + (i + 1) + " / " + STEPS.length + " · " + STEPS[i].t }),
          head(i >= 1), el("div", { class: "st-body" }, body)
        ]));
    }

    var stages = STEPS.map(function (s, i) { return stage(i); });
    var items = STEPS.map(function (s, i) {
      var btn = el("button", { type: "button", class: "st-step-btn", onclick: function () { goTo(i, true); } }, [
        el("span", { class: "st-num mono", text: String(i + 1).padStart(2, "0") }),
        el("span", { class: "st-step-t", text: s.t })
      ]);
      return el("li", { class: "st-step", style: "--i:" + i }, [btn, el("p", { class: "st-step-d", text: s.d })]);
    });
    var rail = el("span", { class: "st-line-fill", "aria-hidden": "true" });
    var list = el("ol", { class: "st-steps" }, items);
    var left = el("div", { class: "st-left" }, [el("div", { class: "st-line", "aria-hidden": "true" }, rail), list]);
    stages.forEach(function (s, i) { s.style.setProperty("--i", String(i)); });
    var panel = el("div", { class: "st-panel" }, stages);
    var pin = el("div", { class: "st-pin" }, [left, panel]);
    var outer = el("div", { class: "st-outer" }, pin);
    var sec = el("section", { class: "hm-sec st", "aria-label": "一篇论文的旅程", "data-mode": "stack" }, [
      el("div", { class: "hm-eyebrow", text: "Pipeline · 单篇追踪" }),
      el("h2", { class: "hm-h2", text: "一篇论文的旅程" }),
      el("p", { class: "st-lede", text: "跟着库里的一篇真实论文，从抓取到进入周报与地图，看每一步给它留下了什么。" }),
      outer
    ]);

    var cur = -1, mode = "stack", raf = 0, bound = false;

    function setActive(i) {
      if (i === cur) return;
      cur = i;
      items.forEach(function (li, k) {
        li.classList.toggle("is-on", k === i);
        li.querySelector(".st-step-btn").setAttribute("aria-current", k === i ? "step" : "false");
      });
      stages.forEach(function (s, k) {
        var on = k === i;
        s.classList.toggle("is-on", on);
        s.setAttribute("aria-hidden", on ? "false" : "true");
        if (on) M.replay(s); else M.unplay(s);
      });
      rail.style.transform = "scaleY(" + (i / (STEPS.length - 1)) + ")";
    }

    function calc() {
      raf = 0;
      if (mode !== "sticky" || !sec.isConnected) return;
      var r = outer.getBoundingClientRect();
      var vh = window.innerHeight || 800;
      var d = -r.top;
      var idx = Math.floor(d / (vh * STEP_VH));
      setActive(Math.max(0, Math.min(STEPS.length - 1, idx)));
      // 落位：进入视口时面板自下而上到位（不支持 view-timeline 时由进度变量驱动同一组 transform）
      var land = Math.max(0, Math.min(1, (vh - r.top) / (vh * 0.5)));
      outer.style.setProperty("--st-land", String(land));
    }
    function onScroll() { if (!raf) raf = requestAnimationFrame(calc); }

    function bind() {
      if (bound) return;
      bound = true;
      window.addEventListener("scroll", onScroll, { passive: true });
      window.addEventListener("resize", onScroll);
    }
    function unbind() {
      if (!bound) return;
      bound = false;
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
    }
    function applyMode() {
      var next = (!reduced && wq && wq.matches) ? "sticky" : "stack";
      mode = next;
      sec.setAttribute("data-mode", next);
      if (next === "sticky") {
        cur = -1; bind(); calc();
        if (cur < 0) setActive(0);
      } else {
        unbind();
        cur = -1;
        items.forEach(function (li) { li.classList.add("is-on"); li.querySelector(".st-step-btn").setAttribute("aria-current", "false"); });
        stages.forEach(function (s) {
          s.classList.add("is-on"); s.setAttribute("aria-hidden", "false");
          // 堆叠形态：进入视口时各面板依次播放一次入场（reduced 无隐藏初态，直接是终态）
          if (reduced) M.play(s); else { M.unplay(s); M.watch(s, function () { if (mode === "stack") M.play(s); }, null, { once: true, threshold: 0.15 }); }
        });
        rail.style.transform = "scaleY(1)";
      }
    }
    function goTo(i, user) {
      if (mode !== "sticky") {
        stages[i].scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
        return;
      }
      var vh = window.innerHeight || 800;
      var top = outer.getBoundingClientRect().top + (window.pageYOffset || 0);
      window.scrollTo({ top: top + i * vh * STEP_VH + 2, behavior: reduced ? "auto" : "smooth" });
    }

    // 键盘：上下方向键在步骤按钮间移动并跳转
    list.addEventListener("keydown", function (ev) {
      var k = ev.key;
      if (k !== "ArrowDown" && k !== "ArrowUp") return;
      var btns = Array.prototype.slice.call(list.querySelectorAll(".st-step-btn"));
      var at = btns.indexOf(document.activeElement);
      if (at < 0) return;
      var nx = Math.max(0, Math.min(btns.length - 1, at + (k === "ArrowDown" ? 1 : -1)));
      ev.preventDefault();
      btns[nx].focus();
    });

    applyMode();
    if (wq && !reduced) {
      var onChange = function () { applyMode(); };
      if (wq.addEventListener) wq.addEventListener("change", onChange); else if (wq.addListener) wq.addListener(onChange);
      M.onCleanup(function () {
        if (wq.removeEventListener) wq.removeEventListener("change", onChange); else if (wq.removeListener) wq.removeListener(onChange);
      });
    }
    M.onCleanup(unbind);
    return sec;
  }

  window.AIVCSteps = { section: section };
})();
