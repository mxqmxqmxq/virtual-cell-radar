/* AIVC 首页「流水线如何工作」演示区块：标签页 + 单舞台 + 自动轮播 + 暂停。
 * 仅暴露 window.AIVCDemos.section(api)。所有文本经 api.el 的 text 输出。
 * 4 个演示均为声明式播放容器（data-mo-demo / data-playing + --mo-delay），由 CSS 延迟编排；
 * 无 html.motion（reduced-motion / 无 IntersectionObserver）时不轮播，4 个演示纵向排列并直接显示终态。 */
(function () {
  "use strict";

  var PAUSE_LABEL = "暂停演示";
  var PLAY_LABEL = "播放演示";
  var SWAP_MS = 350;          // 面板先出后入：各 350ms
  var SVGNS = "http://www.w3.org/2000/svg";

  function sec(v) { return (Math.round(v * 1000) / 1000) + "s"; }
  function delay(node, v) { node.style.setProperty("--mo-delay", sec(v)); return node; }
  function doneAt(node, v) { node.style.setProperty("--mo-done", sec(v)); return node; }
  function trunc(s, n) {
    s = String(s == null ? "" : s);
    return s.length > n ? s.slice(0, n) + "…" : s;
  }
  function svgEl(tag, attrs) {
    var n = document.createElementNS(SVGNS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }

  // ---------- 面板外壳：舞台（播放容器）+ 说明 ----------
  function makePanel(api, id, title, desc, kind, stageKids) {
    var stage = api.el("div", { class: "dm-stage dm-stage-" + kind, "data-mo-demo": "", "aria-hidden": "true" }, [
      api.el("div", { class: "dm-glow dm-glow-a" }),
      api.el("div", { class: "dm-glow dm-glow-b" }),
      api.el("div", { class: "dm-inner" }, stageKids)
    ]);
    var panel = api.el("div", { class: "dm-panel", id: "dm-panel-" + id }, [
      api.el("article", { class: "card dm-card" }, [
        stage,
        api.el("div", { class: "dm-body" }, [
          api.el("h3", { text: title }),
          api.el("p", { text: desc })
        ])
      ])
    ]);
    return { panel: panel, stage: stage };
  }

  // ======================================================================
  // 1) 流水线：4 节点 + 3 段 SVG 连线 + 模拟指针点击「LLM 打分」
  //    7.2s 周期；指针 5%–17% 移入，19.5% 按下，41% 移出。
  // ======================================================================
  var PIPE_DUR = 7.2;
  function buildPipeline(api, H) {
    var demo = H.demo || {};
    var papers = api.arr(demo.pipeline_ids).map(function (id) { return api.byId[id]; }).filter(Boolean);
    var prog = H.progress || {};
    var threshold = api.D.meta && api.D.meta.must_read_threshold;
    var stages = ["抓取", "关键词预筛", "去重", "LLM 打分"];
    // 各节点点亮时刻 / 连线起点（秒）；指针 19.5% × 7.2 ≈ 1.40s 按下，第 4 节点随后点亮
    var nodeAt = [0.2, 0.55, 0.9, 1.5];
    var wireAt = [0.3, 0.65, 1.0];
    var PRESS_AT = 0.195 * PIPE_DUR;
    var idx = 0, cur = null, shown = false;

    var paperBox = api.el("div", { class: "dm-paper mo-pop" });
    delay(paperBox, 0.05);

    var nodes = [], dets = [];
    var cols = stages.map(function (name, i) {
      var on = api.el("span", { class: "dm-node-on mo-pop", "aria-hidden": "true" }, [
        api.el("span", { class: "dm-node-i", text: String(i + 1) }),
        api.el("span", { class: "dm-node-l", text: name })
      ]);
      delay(on, nodeAt[i]);
      var node = api.el("div", { class: "dm-node" + (i === 3 ? " dm-node-press" : "") }, [
        api.el("span", { class: "dm-node-i", text: String(i + 1) }),
        api.el("span", { class: "dm-node-l", text: name }),
        on
      ]);
      var det = api.el("div", { class: "dm-det" });
      nodes.push(node); dets.push(det);
      return api.el("div", { class: "dm-col" }, [node, det]);
    });

    // 连线：viewBox 高 2 → 描边恰为 2px；pathLength=1 做 stroke-dashoffset 点亮
    var svg = svgEl("svg", { class: "dm-wires", viewBox: "0 0 300 2", preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false" });
    svg.appendChild(svgEl("path", { class: "dm-wire-base", d: "M0 1H300", fill: "none" }));
    for (var w = 0; w < 3; w++) {
      var p = svgEl("path", { class: "dm-wire", d: "M" + (w * 100) + " 1H" + ((w + 1) * 100), pathLength: "1", fill: "none" });
      p.style.setProperty("--mo-delay", sec(wireAt[w]));
      svg.appendChild(p);
    }
    var pointer = api.el("div", { class: "dm-pointer", "aria-hidden": "true" });
    var psvg = svgEl("svg", { viewBox: "0 0 16 20", width: "16", height: "20" });
    psvg.appendChild(svgEl("path", { d: "M1 1l0 14 3.8-3.4 2.6 6 2.4-1.1-2.6-5.9H13z", class: "dm-pointer-shape" }));
    pointer.appendChild(psvg);

    var flow = api.el("div", { class: "dm-flow" }, [
      api.el("div", { class: "dm-track" }, [svg]),
      api.el("div", { class: "dm-cols" }, cols),
      pointer
    ]);

    var funnelItems = [["total", "入库"], ["core", "核心圈"], ["scored", "已打分"], ["must_read", "必读"]]
      .filter(function (q) { return prog[q[0]] !== undefined && prog[q[0]] !== null; })
      .map(function (q, i) {
        var f = api.el("span", { class: "dm-fun mo-reveal" }, [
          api.el("span", { class: "dm-fun-l", text: q[1] }),
          api.el("b", { text: String(prog[q[0]]) })
        ]);
        return delay(f, 2.0 + i * 0.12);
      });
    var funnel = api.el("div", { class: "dm-funnel" }, funnelItems);

    var m = makePanel(api, "pipeline", "抓取 → 预筛 → 去重 → 打分",
      "每篇论文依次经过数据源抓取、关键词预筛、跨源去重和 LLM 打分；演示中的论文与数字都来自当前知识库。",
      "pipe", [paperBox, flow, funnel]);

    function reveal(node, at) { node.classList.add("mo-reveal"); return delay(node, at); }
    function fillDet(i, kids, extra) {
      api.clear(dets[i]);
      var d = api.el("div", { class: "dm-d" }, kids);
      dets[i].appendChild(extra ? extra(d) : reveal(d, nodeAt[i] + 0.12));
    }
    function showPaper(p) {
      api.clear(paperBox);
      if (!p) {
        paperBox.appendChild(api.el("span", { class: "dm-ptitle dm-dim", text: "暂无可演示的论文，抓取后这里会出现真实条目" }));
        return;
      }
      paperBox.appendChild(api.el("span", { class: "tag", text: api.str(p.source) || "source" }));
      paperBox.appendChild(api.el("span", { class: "dm-ptitle", text: api.str(p.title) }));
    }
    function fill(p) {
      dets.forEach(api.clear);
      if (!p) return;
      fillDet(0, [api.el("span", { class: "dm-k", text: api.str(p.source) }), api.el("span", { class: "dm-v", text: api.str(p.date) })]);
      var kws = api.arr(p.matched_keywords).slice(0, 2).map(function (k) { return api.tag(api.str(k), "tag-par"); });
      var kids = kws.length ? kws : [api.el("span", { class: "dm-dim", text: "无命中词" })];
      kids.push(api.el("span", { class: "dm-v", text: "分 " + api.fmtScore(p.keyword_score) }));
      kids.push(api.el("span", { class: "dm-k", text: api.circleLabel(p.circle) }));
      fillDet(1, kids);
      var id = api.str(p.id), pos = id.indexOf(":");
      fillDet(2, [api.el("span", { class: "dm-k", text: pos > 0 ? id.slice(0, pos) + " 键" : "标题匹配" })]);
      // 第 4 步：按下之前显示「待打分」(mo-pending)，按下后弹出分数与「必读」(mo-done)
      var doneT = PRESS_AT + 0.15;
      var pend = api.el("span", { class: "mo-pending dm-pend" }, [api.el("span", { class: "dm-dot mo-pulse" }), api.el("span", { class: "dm-k", text: "待打分" })]);
      doneAt(pend, doneT);
      if (p.scored) {
        var kids3 = [api.el("span", { class: "dm-score", text: api.fmtScore(p.final_score) })];
        var mr = p.must_read || (typeof threshold === "number" && typeof p.final_score === "number" && p.final_score >= threshold);
        if (mr) kids3.push(api.tag("必读", "tag-good"));
        var done = api.el("div", { class: "mo-done dm-d dm-done" }, kids3);
        doneAt(done, doneT);
        api.clear(dets[3]);
        dets[3].appendChild(api.el("div", { class: "dm-swap" }, [pend, done]));
      } else {
        // 未打分：占位一直保留（不被替换），用 reveal 在按下之后出现
        api.clear(dets[3]);
        var w4 = api.el("div", { class: "dm-d" }, [api.el("span", { class: "dm-dot mo-pulse" }), api.el("span", { class: "dm-k", text: "待打分" })]);
        dets[3].appendChild(reveal(w4, doneT));
      }
    }
    function pickNext() {
      cur = papers.length ? papers[idx % papers.length] : null;
      idx++;
      showPaper(cur);
      fill(cur);
    }
    pickNext();
    if (!papers.length) pointer.style.display = "none";

    return {
      label: "流水线", dur: PIPE_DUR, panel: m.panel, stage: m.stage,
      prepare: function () { if (shown && papers.length > 1) pickNext(); shown = true; }
    };
  }

  // ======================================================================
  // 2) 逐字段打标签
  // ======================================================================
  function buildTagging(api, H) {
    var demo = H.demo || {};
    var p = demo.tag_id ? api.byId[demo.tag_id] : null;
    var desc = "标题逐字读入后，圈层、任务、范式、数据集和最终分逐字段打上；还没打分的论文会显示等待状态。";
    if (!p) {
      var m0 = makePanel(api, "tagging", "逐字段打标签", desc, "tag", [
        api.el("div", { class: "dm-empty", text: "知识库里还没有可演示的论文" })
      ]);
      return { label: "打标签", dur: 5, panel: m0.panel, stage: m0.stage };
    }
    var scored = !!demo.tag_scored;
    var T0 = 0.3, TYPE = 0.9, ROW0 = T0 + TYPE + 0.1, ROWSTEP = 0.4;

    var title = api.el("div", { class: "dm-ttitle" }, [api.el("span", { class: "mo-type", text: trunc(api.str(p.title), 70) })]);
    delay(title.firstChild, T0);

    var rowIdx = 0;
    var rows = [];
    function tagsOf(list, fn) {
      var a = api.arr(list);
      return a.length ? a.map(fn) : [api.el("span", { class: "dm-dim", text: "—" })];
    }
    function phBar() { return api.el("span", { class: "dm-phbar" }); }
    function rowBase(label, valueNode) {
      var r = api.el("div", { class: "dm-row mo-reveal" }, [
        api.el("span", { class: "dm-rl", text: label }),
        valueNode
      ]);
      delay(r, ROW0 + rowIdx * ROWSTEP);
      rowIdx++;
      rows.push(r);
      return r;
    }
    // 已打分：占位(mo-pending) → 真值(mo-done)
    function rowScored(label, kids) {
      var at = ROW0 + rowIdx * ROWSTEP;
      var pend = api.el("div", { class: "mo-pending dm-rv" }, [phBar()]);
      var done = api.el("div", { class: "mo-done dm-rv" }, kids);
      doneAt(pend, at + 0.3); doneAt(done, at + 0.3);
      return rowBase(label, api.el("div", { class: "dm-swap dm-rvw" }, [pend, done]));
    }
    function rowPlain(label, kids, ph) {
      var r = rowBase(label, api.el("div", { class: "dm-rv dm-rvw" }, kids));
      if (ph) r.classList.add("is-ph");
      return r;
    }
    var wait = null;
    if (scored) {
      rowScored("圈层", [api.el("span", { class: "tag", text: api.circleLabel(p.circle) })]);
      rowScored("任务", tagsOf(api.arr(p.tasks).slice(0, 2), function (t) { return api.tag(api.taskLabel(t), "tag-task"); }));
      rowScored("范式", tagsOf(api.arr(p.paradigms).slice(0, 2), function (t) { return api.tag(api.parLabel(t), "tag-par"); }));
      rowScored("数据集", tagsOf(api.arr(p.datasets_used).slice(0, 2), function (d) { return api.el("span", { class: "tag dm-ds", text: api.str(d) }); }));
      rowScored("最终分", [api.el("span", { class: "dm-score", text: api.fmtScore(p.final_score) }), p.must_read ? api.tag("必读", "tag-good") : null].filter(Boolean));
    } else {
      rowPlain("关键词", tagsOf(api.arr(p.matched_keywords).slice(0, 3), function (k) { return api.tag(api.str(k), "tag-par"); }));
      rowPlain("圈层", [api.el("span", { class: "tag", text: api.circleLabel(p.circle) })]);
      rowPlain("任务", [phBar()], true);
      rowPlain("范式", [phBar()], true);
      rowPlain("最终分", [phBar()], true);
      wait = api.el("div", { class: "dm-wait mo-reveal" }, [api.el("span", { class: "dm-dot mo-pulse" }), api.el("span", { text: "等待 LLM 打分" })]);
      delay(wait, ROW0 + rowIdx * ROWSTEP);
    }
    var tcard = api.el("div", { class: "dm-tcard" }, [
      api.el("div", { class: "dm-tmeta" }, [
        api.el("span", { class: "tag", text: api.str(p.source) || "source" }),
        api.el("span", { class: "dm-dim", text: api.str(p.date) })
      ]),
      title,
      api.el("div", { class: "dm-rows" }, rows),
      wait
    ].filter(Boolean));
    var m = makePanel(api, "tagging", "逐字段打标签", desc, "tag", [tcard]);
    return { label: "打标签", dur: 5, panel: m.panel, stage: m.stage };
  }

  // ======================================================================
  // 3) 热力图：格子按论文数从少到多 mo-pop 错峰点亮，计数同步（读第一个格子动画的 currentTime）
  // ======================================================================
  function buildHeat(api, H) {
    var heat = H.heat || {};
    var pars = api.arr(heat.paradigms), tasks = api.arr(heat.tasks);
    var cells = api.arr(heat.cells).filter(function (c) { return c && c.n > 0; })
      .sort(function (a, b) { return a.n - b.n; });
    var max = heat.max || (cells.length ? cells[cells.length - 1].n : 1) || 1;
    var desc = "范式 × 任务的矩阵按论文数从少到多逐格点亮，颜色越深说明该组合下已打分的论文越多。";
    var H0 = 0.3, SPAN = 3.4;

    var grid = api.el("div", { class: "dm-hgrid" });
    grid.style.gridTemplateColumns = "repeat(" + Math.max(1, tasks.length) + ", minmax(0, 1fr))";
    grid.style.maxWidth = Math.max(1, tasks.length) * 22 + "px";
    var map = {};
    pars.forEach(function (pp) {
      tasks.forEach(function (tt) {
        var cell = api.el("span", { class: "dm-hc" });
        map[pp.id + "\u0001" + tt.id] = cell;
        grid.appendChild(cell);
      });
    });
    var delays = [];
    var refEl = null;
    cells.forEach(function (cc, i) {
      var host = map[cc.p + "\u0001" + cc.t];
      if (!host) { delays.push(H0 + SPAN); return; }
      var d = H0 + (cells.length > 1 ? SPAN * i / (cells.length - 1) : 0);
      delays.push(d);
      var lit = api.el("span", { class: "dm-hl mo-pop" });
      lit.style.background = "rgba(var(--heat-rgb, 31, 79, 255), " + (0.18 + 0.82 * cc.n / max).toFixed(3) + ")";
      delay(lit, d);
      host.appendChild(lit);
      refEl = lit;
    });
    var kNum = api.el("b", { class: "dm-hk", text: String(cells.length) });
    var side = api.el("div", { class: "dm-hside" }, [
      api.el("div", { class: "dm-hbig" }, [kNum, api.el("span", { class: "dm-hof", text: " / " + cells.length + " 格" })]),
      api.el("div", { class: "dm-hcap", text: cells.length ? "已点亮 · 基于 " + (heat.n_scored || 0) + " 篇已打分" : "打分后这里会亮起来" }),
      api.el("div", { class: "dm-axis", text: "行 范式 · 列 任务" })
    ]);
    var m = makePanel(api, "heat", "范式 × 任务热力图", desc, "heat", [
      (pars.length && tasks.length)
        ? api.el("div", { class: "dm-heat" }, [grid, side])
        : api.el("div", { class: "dm-empty", text: "分类体系尚未载入" })
    ]);

    // 计数同步：每帧读参考格子动画的 currentTime（暂停时自然冻结）
    var raf = 0, count = cells.length;
    function setCount(n) { if (n !== count) { count = n; kNum.textContent = String(n); } }
    function stopCount() { if (raf) cancelAnimationFrame(raf); raf = 0; }
    function tick() {
      raf = 0;
      var a = null;
      try { a = refEl && refEl.getAnimations ? refEl.getAnimations()[0] : null; } catch (e) { a = null; }
      if (!a) { setCount(cells.length); return; }
      var t = (a.currentTime || 0) / 1000;
      var n = 0;
      for (var i = 0; i < delays.length; i++) if (delays[i] <= t) n = i + 1; else break;
      setCount(n);
      if (t < H0 + SPAN + 0.5) raf = requestAnimationFrame(tick); else setCount(cells.length);
    }
    return {
      label: "热力图", dur: 5, panel: m.panel, stage: m.stage,
      onActive: function () {
        if (!cells.length || !refEl) return;
        stopCount(); count = -1; setCount(0); raf = requestAnimationFrame(tick);
      },
      onInactive: function () { stopCount(); setCount(cells.length); },
      dispose: stopCount
    };
  }

  // ======================================================================
  // 4) 周报终端：命令 mo-type，各行 mo-reveal 错峰，光标 mo-blink
  // ======================================================================
  function buildWeekly(api, H) {
    var w = H.weekly || null;
    var desc = "周报由命令一键生成：终端先敲出命令，再把标题和要点逐行写出来。";
    var cmd = api.el("div", { class: "dm-tl dm-tcmd" }, [api.el("span", { class: "dm-lt mo-type", text: "$ uv run python -m aivc weekly" })]);
    delay(cmd.firstChild, 0.3);
    var lines = [];
    if (w) {
      if (w.title) lines.push({ cls: "dm-ttl", text: api.str(w.title) });
      api.arr(w.lines).slice(0, 6).forEach(function (l) { lines.push({ cls: "dm-tln", text: api.str(l) }); });
    } else {
      lines.push({ cls: "dm-tln dm-dim", text: "还没有周报" });
    }
    var L0 = 1.35, STEP = 0.4;
    var kids = [cmd];
    lines.forEach(function (l, i) {
      var d = api.el("div", { class: "dm-tl mo-reveal " + l.cls }, [api.el("span", { class: "dm-lt", text: l.text })]);
      kids.push(delay(d, L0 + STEP * i));
    });
    var cursor = api.el("div", { class: "dm-tl mo-reveal" }, [api.el("span", { class: "dm-cursor mo-blink" })]);
    kids.push(delay(cursor, L0 + STEP * lines.length));
    var term = api.el("div", { class: "dm-term" }, [
      api.el("div", { class: "dm-tbar" }, [
        api.el("i"), api.el("i"), api.el("i"),
        api.el("span", { class: "dm-tname", text: w && w.id ? api.str(w.id) : "weekly" })
      ]),
      api.el("div", { class: "dm-tbody" }, kids)
    ]);
    var m = makePanel(api, "weekly", "周报逐行生成", desc, "term", [term]);
    return { label: "周报", dur: 5, panel: m.panel, stage: m.stage };
  }

  // ======================================================================
  // 区块
  // ======================================================================
  window.AIVCDemos = {
    section: function (api) {
      var H = api && api.D && api.D.home;
      if (!H) return null;
      var live = !(api.motion && api.motion.reduced);

      var demos = [];
      [buildPipeline, buildTagging, buildHeat, buildWeekly].forEach(function (b) {
        try { var d = b(api, H); if (d) demos.push(d); }
        catch (e) { if (window.console) console.warn("[AIVCDemos] 演示构建失败", e); }
      });
      if (!demos.length) return null;

      var wrap = api.el("div", { class: "dm-wrap" }, demos.map(function (d) { return d.panel; }));
      var headKids = [api.el("div", {}, [
        api.el("div", { class: "eyebrow", text: "How it works" }),
        api.el("h2", {}, ["从抓取到周报，", api.el("span", { class: "hl-text", text: "每一步都可追溯" })]),
        api.el("p", { class: "lede", text: "下面的演示用的都是知识库里的真实论文和统计数字，不是示意假数据。" })
      ])];
      var section = api.el("section", { class: "section dm-section" + (live ? " dm-live" : " dm-static") });

      if (!live) {
        // reduced：不轮播、不循环，4 个演示纵向排列，直接显示终态
        section.appendChild(api.el("div", { class: "section-head" }, headKids));
        section.appendChild(wrap);
        return section;
      }

      var cur = -1, userPaused = false, inView = false, started = false, swapTimer = 0;
      var tabs = [], fills = [];
      var toggle = api.el("button", {
        class: "btn dm-toggle", type: "button", "aria-pressed": "false", text: PAUSE_LABEL,
        onclick: function () {
          userPaused = !userPaused;
          toggle.setAttribute("aria-pressed", userPaused ? "true" : "false");
          toggle.textContent = userPaused ? PLAY_LABEL : PAUSE_LABEL;
          applyPause();
        }
      });
      headKids.push(toggle);

      function applyPause() {
        section.classList.toggle("mo-paused", userPaused || !inView || document.hidden);
      }

      var list = api.el("div", { class: "dm-tabs", role: "group", "aria-label": "演示选择" });
      demos.forEach(function (d, i) {
        var f = api.el("i", {});
        f.style.setProperty("--dm-dur", sec(d.dur - SWAP_MS / 1000));  // 动画自带 .35s 延迟，总长恰为 dur
        f.addEventListener("animationend", function (e) {
          if (e.target !== f || cur !== i) return;
          select((i + 1) % demos.length);
        });
        fills.push(f);
        var b = api.el("button", {
          class: "dm-tab", type: "button", "aria-pressed": "false",
          "aria-controls": d.panel.id,
          onclick: function () { select(i); }
        }, [api.el("span", { class: "dm-tab-t", text: d.label }), api.el("span", { class: "dm-tprog" }, [f])]);
        tabs.push(b);
        list.appendChild(b);
      });
      list.addEventListener("keydown", function (e) {
        var k = e.key, n = demos.length, i = tabs.indexOf(document.activeElement);
        if (i < 0) return;
        var j = k === "ArrowRight" ? (i + 1) % n : k === "ArrowLeft" ? (i - 1 + n) % n : k === "Home" ? 0 : k === "End" ? n - 1 : -1;
        if (j < 0) return;
        e.preventDefault();
        tabs[j].focus();
        select(j);
      });

      function scrollTabIntoView(i) {
        var t = tabs[i];
        if (list.scrollWidth <= list.clientWidth) return;
        var left = t.offsetLeft - (list.clientWidth - t.offsetWidth) / 2;
        try { list.scrollTo({ left: Math.max(0, left), behavior: "smooth" }); } catch (e) { list.scrollLeft = Math.max(0, left); }
      }

      function start(i) {
        var d = demos[i];
        if (d.prepare) d.prepare();
        api.motion.replay(d.stage);
        if (d.onActive) d.onActive();
      }

      function select(i) {
        var old = cur;
        cur = i;
        tabs.forEach(function (t, k) {
          t.setAttribute("aria-pressed", k === i ? "true" : "false");
          t.classList.toggle("is-done", k < i);
        });
        // 重启当前标签的进度（先全部移除，强制回流，再给当前标签加 is-run）
        fills.forEach(function (f) { f.parentNode.parentNode.classList.remove("is-run"); });
        void list.offsetWidth;
        tabs[i].classList.add("is-run");
        scrollTabIntoView(i);
        clearTimeout(swapTimer);
        if (old === i) { start(i); return; }
        if (old < 0) {
          demos.forEach(function (d, k) { d.panel.classList.toggle("is-active", k === i); });
          start(i); return;
        }
        // 旧面板淡出 350ms，之后新面板淡入并开始播放
        demos[old].panel.classList.remove("is-active");
        demos[i].panel.classList.add("is-active");
        swapTimer = setTimeout(function () {
          demos.forEach(function (d, k) {
            if (k !== i) { api.motion.unplay(d.stage); if (d.onInactive) d.onInactive(); }
          });
          start(i);
        }, SWAP_MS);
      }

      section.appendChild(api.el("div", { class: "section-head" }, headKids));
      section.appendChild(list);
      section.appendChild(wrap);
      // 初始：第一个面板可见（未播放），进入视口 ≥30% 才开始；离开暂停，续播不重头
      demos[0].panel.classList.add("is-active");
      tabs[0].setAttribute("aria-pressed", "true");
      applyPause();

      var u = api.motion.watch(wrap, function () {
        inView = true;
        if (!started) { started = true; select(0); }
        applyPause();
      }, function () { inView = false; applyPause(); }, { threshold: 0.3 });
      if (typeof u === "function") api.motion.onCleanup(u);
      function onVis() { applyPause(); }
      document.addEventListener("visibilitychange", onVis);
      api.motion.onCleanup(function () {
        document.removeEventListener("visibilitychange", onVis);
        clearTimeout(swapTimer);
        demos.forEach(function (d) { try { if (d.dispose) d.dispose(); } catch (e) { /* ignore */ } });
      });
      return section;
    }
  };
})();
