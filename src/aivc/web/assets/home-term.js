/* AIVC 首页「命令行四步」深色终端区块 + 「常见问题」手风琴区块。
 * 仅暴露 window.AIVCTerm = { section(api), faq(api) }，各返回 section 元素或 null，内部异常自行捕获。
 * 文本一律经 api.el 的 text；定时器与监听经 api.motion.onCleanup 注销。
 * 动效手法（自写实现，参数取自实测记录 animation-spec-2.md）：
 *   R-TERM-02/03  命令行 mo-type 擦出（约 .9s steps）、光标 1s steps(1) 闪烁
 *   R-FW-01       标签 5.0s 自动前进、底部光带线性走满、行 150ms 错峰；手动点过后停止；reduced 下不自动前进
 *   R-HOVER-07    标签 opacity .6→1，150ms
 *   R-HOVER-03    深色次按钮 150ms，按下 scale .98
 *   R-FAQ-01..04  原生 details[name] 互斥；内容高度 300ms ease-in-out（全站唯一动画高度处）；+/− 即时互换
 */
(function () {
  "use strict";

  var TAB_MS = 5000; // R-FW-01：每个标签 5.0s

  function safeRun(name, fn) {
    try { return fn(); }
    catch (e) { if (window.console) console.warn("[AIVCTerm] " + name + " 构建失败", e); return null; }
  }
  function num(v) { return typeof v === "number" && isFinite(v) ? v : null; }
  function fmtN(v) { var n = num(v); return n === null ? "—" : String(n); }

  // ---------- 终端内容（命令逐字取自 README.md） ----------
  function buildTabs(api) {
    var D = api.D || {};
    var H = D.home || {};
    var meta = D.meta || {};
    var P = H.progress || {};
    var W = H.weekly || {};
    var thr = num(meta.must_read_threshold);

    var active = (H.sources || []).filter(function (s) { return s && s.status === "active" && num(s.count) !== null; })
      .sort(function (a, b) { return b.count - a.count; });
    var srcText = active.slice(0, 3).map(function (s) { return (s.label || s.id) + " " + s.count + " 篇"; }).join("、");

    var tabs = [];
    // 抓取：README 第 31 行 `uv run python -m aivc update`
    tabs.push({
      id: "fetch", label: "抓取", cmd: "uv run python -m aivc update",
      lines: [
        "入库论文共 " + fmtN(P.total) + " 篇，本周新增 " + fmtN(P.this_week_new) + " 篇",
        active.length ? ("启用的来源 " + active.length + " 个：" + srcText) : null
      ]
    });
    // 打分：README 第 111 行
    tabs.push({
      id: "score", label: "打分", cmd: "uv run python -m aivc score",
      lines: [
        "已打分 " + fmtN(P.scored) + " / " + fmtN(P.total) + " 篇，未打分 " + fmtN(P.unscored) + " 篇",
        "必读 " + fmtN(P.must_read) + " 篇" + (thr !== null ? "（最终分 ≥ " + thr + "）" : ""),
        "新发布的数据集或基准 " + fmtN(P.new_datasets) + " 个"
      ]
    });
    // 周报：README 第 137 行
    tabs.push({
      id: "weekly", label: "周报", cmd: "uv run python -m aivc weekly",
      lines: [
        W.title ? ("最新周报：" + W.title) : null,
        W.id ? ("周 id " + W.id + "，要点 " + ((W.lines || []).length) + " 条") : null
      ]
    });
    // 建站：README 第 140 行
    tabs.push({
      id: "site", label: "建站", cmd: "uv run python -m aivc build-site",
      lines: [
        "已导出 site/data/*.json，供本站读取",
        meta.generated_at ? ("数据生成于 " + meta.generated_at) : null
      ]
    });
    tabs.forEach(function (t) { t.lines = t.lines.filter(Boolean); });
    return tabs;
  }

  function section(api) {
    return safeRun("section", function () {
      if (!api || !api.D || !api.D.home) return null;
      var el = api.el, M = api.motion, reduced = !!M.reduced;
      var tabs = buildTabs(api);
      if (!tabs.length) return null;

      var uid = "tm" + Math.floor(Math.random() * 1e6);
      var cur = 0;
      var userTouched = false;
      var inView = false;
      var timer = 0, startedAt = 0, remain = TAB_MS;

      var tablist = el("div", { class: "tm-tabs", role: "tablist", "aria-label": "流水线命令" });
      var tabEls = [], panels = [], bars = [];
      var copyBtn, copyTimer = 0;

      tabs.forEach(function (t, i) {
        var bar = el("i", { class: "tm-bar", "aria-hidden": "true" });
        var b = el("button", {
          type: "button", class: "tm-tab", role: "tab", id: uid + "-t" + i,
          "aria-controls": uid + "-p" + i, "aria-selected": "false", tabindex: "-1"
        }, [el("span", { class: "tm-tab-n", text: String(i + 1).padStart(2, "0") }), el("span", { text: t.label }), bar]);
        b.addEventListener("click", function () { userTouched = true; stopTimer(); select(i, true); });
        b.addEventListener("keydown", function (ev) {
          var k = ev.key, n = -1;
          if (k === "ArrowRight") n = (i + 1) % tabs.length;
          else if (k === "ArrowLeft") n = (i - 1 + tabs.length) % tabs.length;
          else if (k === "Home") n = 0;
          else if (k === "End") n = tabs.length - 1;
          if (n < 0) return;
          ev.preventDefault();
          userTouched = true; stopTimer();
          select(n, true); tabEls[n].focus();
        });
        tabEls.push(b); bars.push(bar);
        tablist.appendChild(b);

        var cmdText = el("span", { class: "tm-cmd-t mo-type", text: t.cmd });
        var lines = t.lines.map(function (ln, k) {
          var li = el("div", { class: "tm-out mo-reveal", text: ln });
          li.style.setProperty("--mo-delay", (950 + k * 150) + "ms"); // R-FW：行 150ms 错峰
          return li;
        });
        var cursor = el("span", { class: "tm-cursor mo-blink", "aria-hidden": "true" });
        var cmdLine = el("div", { class: "tm-cmd" }, [el("span", { class: "tm-prompt", "aria-hidden": "true", text: "$" }), cmdText]);
        var p = el("div", {
          class: "tm-panel", role: "tabpanel", id: uid + "-p" + i, "aria-labelledby": uid + "-t" + i,
          "data-mo-demo": "", hidden: "hidden", tabindex: "0"
        }, [cmdLine, el("div", { class: "tm-outs" }, lines), el("div", { class: "tm-prompt-line", "aria-hidden": "true" }, [el("span", { class: "tm-prompt", text: "$" }), cursor])]);
        p._cmd = t.cmd; p._cmdText = cmdText;
        panels.push(p);
      });

      function stopTimer() {
        if (timer) { clearTimeout(timer); timer = 0; }
        bars.forEach(function (b) { b.classList.remove("is-run"); b.style.removeProperty("animation-play-state"); });
        remain = TAB_MS;
      }
      function runBar(restart) {
        var b = bars[cur];
        bars.forEach(function (x, i) { if (i !== cur) x.classList.remove("is-run"); });
        if (restart) { b.classList.remove("is-run"); void b.offsetWidth; remain = TAB_MS; }
        b.style.animationDuration = TAB_MS + "ms";
        b.classList.add("is-run");
        b.style.animationPlayState = "running";
        startedAt = Date.now();
        clearTimeout(timer);
        timer = setTimeout(function () { select((cur + 1) % tabs.length, true); }, remain);
      }
      function pauseTimer() {
        if (!timer) return;
        clearTimeout(timer); timer = 0;
        remain = Math.max(0, remain - (Date.now() - startedAt));
        bars[cur].style.animationPlayState = "paused";
      }
      function canAuto() { return !reduced && !userTouched; }
      function syncAuto() {
        if (!canAuto()) return;
        if (inView && !document.hidden) { if (!timer) runBar(false); } else pauseTimer();
      }

      function select(i, animate) {
        cur = i;
        tabEls.forEach(function (b, k) {
          var on = k === i;
          b.setAttribute("aria-selected", on ? "true" : "false");
          b.tabIndex = on ? 0 : -1;
        });
        panels.forEach(function (p, k) { p.hidden = k !== i; });
        if (animate && !reduced) {
          if (inView || animate === true) M.replay(panels[i]);
        }
        if (canAuto()) { if (inView && !document.hidden) runBar(true); }
      }

      // 复制命令：navigator.clipboard，失败则选中文本
      function selectCmd() {
        try {
          var r = document.createRange(); r.selectNodeContents(panels[cur]._cmdText);
          var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
          return true;
        } catch (e) { return false; }
      }
      function copied(ok) {
        copyBtn.querySelector(".tm-copy-t").textContent = ok ? "已复制" : "已选中";
        clearTimeout(copyTimer);
        copyTimer = setTimeout(function () { copyBtn.querySelector(".tm-copy-t").textContent = "复制命令"; }, 1600);
      }
      copyBtn = el("button", { type: "button", class: "tm-copy", "aria-label": "复制当前命令" }, [
        el("span", { class: "tm-copy-t", text: "复制命令" })
      ]);
      copyBtn.addEventListener("click", function () {
        userTouched = true; stopTimer();
        var text = panels[cur]._cmd;
        function fallback() {
          var ok = false;
          if (selectCmd()) { try { ok = document.execCommand("copy"); } catch (e) { ok = false; } }
          if (ok) { copied(true); M.toast("已复制命令"); }
          else { copied(false); M.toast("已选中命令，请按 Ctrl/⌘ + C 复制"); }
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () { copied(true); M.toast("已复制命令"); }, fallback);
        } else fallback();
      });

      var chrome = el("div", { class: "tm-chrome" }, [
        el("span", { class: "tm-dots", "aria-hidden": "true" }, [el("i"), el("i"), el("i")]),
        el("span", { class: "tm-title mono", text: "aivc · 虚拟细胞雷达流水线" }),
        copyBtn
      ]);
      var win = el("div", { class: "tm-win" }, [chrome, tablist].concat(panels));

      var head = el("div", { class: "tm-head" }, [
        el("div", { class: "tm-eyebrow", text: "Command line" }),
        el("h2", { class: "tm-h", text: "四条命令，跑完整条流水线" }),
        el("p", { class: "tm-lede", text: "输出里的数字来自当前知识库的真实统计，命令来自项目 README。" })
      ]);
      M.rise(head, {}); M.rise(win, { delay: 80 });

      var root = el("section", { class: "tm-section", "data-nav-theme": "dark", "aria-label": "命令行四步" },
        [el("div", { class: "tm-in" }, [head, win])]);

      select(0, false);
      var stop = M.watch(root, function () {
        var first = !inView; inView = true;
        if (first && !reduced) M.replay(panels[cur]);
        syncAuto();
      }, function () { inView = false; syncAuto(); }, { threshold: 0.3 });
      var vis = function () { syncAuto(); };
      document.addEventListener("visibilitychange", vis);
      M.onCleanup(function () {
        document.removeEventListener("visibilitychange", vis);
        clearTimeout(timer); clearTimeout(copyTimer);
        if (typeof stop === "function") stop();
      });
      return root;
    });
  }

  // ---------- 常见问题 ----------
  // 页面只显示 README 节名，避免行号失效；行号（2026-11 当前）：3、48、74-80、127、301（第 1 条）；122-129、100-106、137-159、140-146、205（其余）。
  function faqItems(api) {
    var meta = (api.D && api.D.meta) || {};
    var thr = num(meta.must_read_threshold);
    return [
      {
        q: "数据从哪些来源抓取？",
        a: "每日自动抓取 arXiv 与 bioRxiv 的新论文，先按关键词预筛，再去重入库；只有通过核心圈或外围圈预筛的条目才会入库。库中还包含来自 OpenReview 的 ICLR 投稿条目。行业新闻来自已验证可用的公开 RSS 源（见 #/news），只保存标题、链接与 LLM 生成的中文摘要。medRxiv 与期刊 RSS 目前未启用。",
        src: "README「流水线说明」「命令一览」「配置说明」"
      },
      {
        q: "打分和「必读」是怎么来的？",
        a: "用 LLM 给每篇论文打相关性分，最终分 = 相关性 × 圈权重（核心圈 1.0，外围圈 0.6）。最终分不低于必读阈值时标为必读" +
          (thr !== null ? "，当前阈值为 " + thr + "。" : "，阈值写在 config/llm.yaml 的 must_read_threshold。"),
        src: "README「流水线说明」「配置说明」"
      },
      {
        q: "没有打分的论文会怎么显示？",
        a: "论文照常抓取、入库，标记为未打分。日报和周报按关键词分排序并注明未打分，网站上会明确标出「未打分」和关键词分。运行 uv run python -m aivc score 可以补打分。",
        src: "README「没有可用打分后端时的行为」「Phase 2：周报与本地网站」"
      },
      {
        q: "周报是怎么生成的？",
        a: "周为自然周（周一到周日，按 ISO 周编号），按论文入库日期划分。运行 uv run python -m aivc weekly 生成最近一个完整自然周的周报，内容包括本周概览、Top 论文、新数据集和范式分布。",
        src: "README「Phase 2：周报与本地网站」"
      },
      {
        q: "网站怎么在本地预览？",
        a: "先运行 uv run python -m aivc build-site 把数据导出到 site/，再运行 uv run python -m aivc serve 启动本地服务，然后在浏览器打开 http://localhost:8000。serve --port 9001 可改用其他端口。",
        src: "README「Phase 2：周报与本地网站」"
      },
      {
        q: "开启「减少动态效果」后首页是什么样？",
        a: "系统开启减少动态效果（prefers-reduced-motion）时，首页不播放循环和位移动画，直接显示终态；循环动画离开视口或页面隐藏时也会暂停。",
        src: "README「首页结构」"
      },
      {
        q: "怎么只看和我课题相关的论文？",
        a: "点导航栏的「我的课题」，添加课题并填入关键词，命中标题、摘要或关键词的论文会在论文流里打上「课题」标签，也可以按课题筛选，周报页会列出本周命中的论文。课题只保存在你自己的浏览器里，不会上传；想分享可以导出链接或文件，对方确认后才会合并。",
        src: "README「我的课题」"
      }
    ];
  }

  function faq(api) {
    return safeRun("faq", function () {
      if (!api || !api.el) return null;
      var el = api.el, M = api.motion;
      var items = faqItems(api);
      var gname = "fq-" + Math.floor(Math.random() * 1e6);
      var nameOK = false;
      try { nameOK = "name" in document.createElement("details"); } catch (e) { nameOK = false; }

      var list = el("div", { class: "fq-list" });
      var dets = [];
      items.forEach(function (it, i) {
        var d = el("details", { class: "fq-item", name: gname }, [
          el("summary", { class: "fq-sum" }, [
            el("span", { class: "fq-q", text: it.q }),
            el("span", { class: "fq-sign", "aria-hidden": "true" }, [el("i", { class: "fq-plus", text: "+" }), el("i", { class: "fq-minus", text: "−" })])
          ]),
          el("div", { class: "fq-body" }, el("div", { class: "fq-body-in" }, [
            el("p", { class: "fq-a", text: it.a }),
            el("p", { class: "fq-src", text: "出处：" + it.src })
          ]))
        ]);
        if (!nameOK) {
          d.addEventListener("toggle", function () {
            if (!d.open) return;
            dets.forEach(function (o) { if (o !== d && o.open) o.open = false; });
          });
        }
        dets.push(d); list.appendChild(d);
      });

      var head = el("div", { class: "fq-head" }, [
        el("div", { class: "fq-eyebrow", text: "FAQ" }),
        el("h2", { class: "fq-h", text: "常见问题" })
      ]);
      M.rise(head, {}); M.rise(list, { delay: 80 });
      return el("section", { class: "fq-section", "aria-label": "常见问题" }, [head, list]);
    });
  }

  window.AIVCTerm = { section: section, faq: faq };
})();
