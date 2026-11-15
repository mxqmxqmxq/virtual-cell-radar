/*
 * topics-ui.js — 「我的课题」面板界面（依赖 window.AIVCTopics 与 window.AIVC，均在调用时才读取）。
 * 浏览器里挂 window.AIVCTopicsUI。所有用户输入、导入内容、课题名、词项一律走 textContent，不做 HTML 字符串注入。
 *
 * 对外接口
 *   open({papers, opener, onClose})
 *       打开「我的课题」面板（原生 <dialog> + showModal）。
 *       papers  用于实时统计命中篇数的论文数组，缺省取 window.AIVC.D.papers
 *       opener  触发按钮；面板关闭后焦点回到它
 *       onClose 面板关闭后回调（无论 Esc、遮罩、按钮、路由切换）
 *       已打开时再次调用只更新 papers 并保持面板。
 *   close()                       关闭面板（带收起动效）；未打开时无操作。
 *   isOpen()                      -> bool
 *   confirmImport(topics, {onDone})
 *       弹出导入确认框，列出每个课题名称与全部词项；按钮「合并到我的课题」「取消」，默认焦点在「取消」。
 *       只有点「合并到我的课题」才调用 AIVCTopics.mergeImported(topics) 并提示实际加入数；
 *       onDone(added) 在确认后调用（added 为实际加入数）；取消或 Esc 时调用 onDone(null)，不改动任何数据。
 *       topics 为 [{name, terms}]（decodeShare / importJSON 的结果）。
 *
 * 依赖的 AIVCTopics 方法：LIMITS validateTopic addTopic updateTopic removeTopic setEnabled
 *   setPresetEnabled getPresets allTopics countMatches encodeShare exportJSON importJSON mergeImported onChange
 * 类名一律 tp- 前缀；样式在 topics.css。
 */
(function (root) {
  "use strict";

  var state = null;   // 打开中的面板状态；null 表示未打开
  var confirmState = null;

  function T() { return root.AIVCTopics; }
  function A() { return root.AIVC; }
  function el(tag, attrs, kids) { return A().el(tag, attrs, kids); }

  function reducedMotion() {
    try {
      if (A() && A().motion && A().motion.reduced) return true;
      return !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (e) { return false; }
  }
  function toast(msg) {
    try { A().motion.toast(msg); } catch (e) { /* ignore */ }
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  /* 输入框文本 -> 词项数组：换行、英文/中文逗号、分号分隔 */
  function parseTerms(text) {
    return String(text || "").split(/[\n\r,，;；、]+/).map(function (s) { return s.trim(); }).filter(function (s) { return s; });
  }
  function papersOf(opts) {
    var p = opts && opts.papers;
    if (Array.isArray(p)) return p;
    var d = A() && A().D && A().D.papers;
    return Array.isArray(d) ? d : [];
  }

  /* 带收起动效地关闭 dialog；动效关闭或不支持时直接关 */
  function closeDialog(dlg) {
    if (!dlg || !dlg.open) return;
    if (reducedMotion() || dlg.classList.contains("tp-closing")) { dlg.close(); return; }
    dlg.classList.add("tp-closing");
    var done = false;
    function fin() { if (done) return; done = true; dlg.classList.remove("tp-closing"); if (dlg.open) dlg.close(); }
    dlg.addEventListener("animationend", function (e) { if (e.target === dlg) fin(); });
    setTimeout(fin, 320);
  }

  function makeDialog(cls, label) {
    var dlg = el("dialog", { "class": cls, "aria-label": label });
    dlg.addEventListener("cancel", function (e) { e.preventDefault(); closeDialog(dlg); });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) closeDialog(dlg); });
    return dlg;
  }

  /* ------------------------------------------------------------------ */
  /* 面板                                                                 */
  /* ------------------------------------------------------------------ */

  function open(opts) {
    opts = opts || {};
    if (!T() || !A()) return;
    if (state) { state.papers = papersOf(opts); refreshLists(); return; }

    var s = state = {
      opts: opts, papers: papersOf(opts), opener: opts.opener || null,
      editing: null,        // null | {id: string|null}
      confirmDel: null,     // 正在二次确认删除的课题 id
      debounce: 0, unsub: null, dlg: null,
      listBox: null, formBox: null, presetBox: null, status: null, linkBox: null,
      f: null               // 表单控件引用
    };

    var dlg = s.dlg = makeDialog("tp-panel", "我的课题");
    var inner = el("div", { "class": "tp-in" });
    var closeBtn = el("button", { type: "button", "class": "btn btn-ghost tp-close", "aria-label": "关闭", text: "关闭 ✕", onclick: function () { closeDialog(dlg); } });
    inner.appendChild(el("div", { "class": "tp-head" }, [
      el("h2", { "class": "tp-title", text: "我的课题" }),
      closeBtn
    ]));
    inner.appendChild(el("p", { "class": "tp-lead", text: "按你关心的方向给论文打标：词项命中标题、摘要或关键词即算命中。课题只在你的浏览器里生效。" }));

    s.listBox = el("section", { "class": "tp-sec", "aria-labelledby": "tp-h-list" });
    s.formBox = el("section", { "class": "tp-sec tp-formsec", "aria-labelledby": "tp-h-form" });
    s.presetBox = el("section", { "class": "tp-sec", "aria-labelledby": "tp-h-preset" });
    inner.appendChild(s.listBox);
    inner.appendChild(s.formBox);
    inner.appendChild(s.presetBox);
    inner.appendChild(buildShareSection(s));
    inner.appendChild(el("section", { "class": "tp-sec tp-privacy", "aria-labelledby": "tp-h-priv" }, [
      el("h3", { "class": "tp-h", id: "tp-h-priv", text: "隐私" }),
      el("p", { "class": "tp-note", text: "课题只保存在本浏览器的 localStorage 里，不会上传到任何服务器。分享链接把课题内容放在网址 # 之后的部分，浏览器不会把这部分发给网站服务器；谁拿到链接，谁就能看到其中的课题。" })
    ]));
    dlg.appendChild(inner);
    document.body.appendChild(dlg);

    renderForm(s);
    refreshLists();

    s.unsub = T().onChange(function () { if (state === s) refreshLists(); });

    dlg.addEventListener("close", function () {
      clearTimeout(s.debounce);
      if (s.unsub) { try { s.unsub(); } catch (e) { /* ignore */ } s.unsub = null; }
      if (dlg.parentNode) dlg.parentNode.removeChild(dlg);
      if (state === s) state = null;
      var op = s.opener;
      if (op && op.isConnected && typeof op.focus === "function") { try { op.focus(); } catch (e) { /* ignore */ } }
      if (typeof s.opts.onClose === "function") { try { s.opts.onClose(); } catch (e) { /* ignore */ } }
    });

    try { dlg.showModal(); } catch (e) { dlg.setAttribute("open", ""); }
    try { closeBtn.focus(); } catch (e) { /* ignore */ }
  }

  function close() { if (state) closeDialog(state.dlg); }
  function isOpen() { return !!state; }

  /* ---------------- 列表 + 预设（订阅 onChange 时重绘） ---------------- */

  function refreshLists() {
    var s = state; if (!s) return;
    var active = document.activeElement;
    var key = active && active.getAttribute && s.dlg.contains(active) ? active.getAttribute("data-tpkey") : null;
    renderList(s);
    renderPresets(s);
    if (key) {
      var again = s.dlg.querySelector('[data-tpkey="' + key.replace(/"/g, "") + '"]');
      if (again && typeof again.focus === "function") again.focus();
    }
  }

  function termChips(terms) {
    var wrap = el("div", { "class": "tp-terms" });
    (terms || []).forEach(function (t) { wrap.appendChild(el("span", { "class": "tag tp-term", text: t })); });
    return wrap;
  }

  function switchCtl(checked, label, key, onchange) {
    var inp = el("input", { type: "checkbox", "class": "tp-switch", role: "switch", "aria-label": label, "data-tpkey": key });
    inp.checked = !!checked;
    inp.addEventListener("change", function () { onchange(inp.checked); });
    return inp;
  }

  function renderList(s) {
    var box = s.listBox; clear(box);
    var users = T().allTopics().filter(function (t) { return t.kind === "user"; });
    var L = T().LIMITS;
    box.appendChild(el("div", { "class": "tp-sechead" }, [
      el("h3", { "class": "tp-h", id: "tp-h-list", text: "我的课题（" + users.length + "/" + L.maxTopics + "）" }),
      el("button", { type: "button", "class": "btn btn-primary tp-new", "data-tpkey": "new", text: "+ 新建课题", onclick: function () { startEdit(s, null); } })
    ]));
    if (!users.length) {
      box.appendChild(el("p", { "class": "tp-empty", text: "还没有自己的课题。点「新建课题」，或启用下方的预设。" }));
      return;
    }
    var ul = el("ul", { "class": "tp-list" });
    users.forEach(function (t) {
      var n = T().countMatches(s.papers, t.terms);
      var confirming = s.confirmDel === t.id;
      var acts = el("div", { "class": "tp-acts" });
      if (confirming) {
        acts.appendChild(el("span", { "class": "tp-warn", text: "删除这个课题？" }));
        acts.appendChild(el("button", { type: "button", "class": "btn tp-danger", "data-tpkey": "del-" + t.id, text: "确认删除", onclick: function () { s.confirmDel = null; if (s.editing && s.editing.id === t.id) { s.editing = null; renderForm(s); } T().removeTopic(t.id); toast("已删除课题「" + t.name + "」"); } }));
        acts.appendChild(el("button", { type: "button", "class": "btn btn-ghost", "data-tpkey": "delno-" + t.id, text: "取消", onclick: function () { s.confirmDel = null; refreshLists(); } }));
      } else {
        acts.appendChild(el("button", { type: "button", "class": "btn btn-ghost", "data-tpkey": "edit-" + t.id, "aria-label": "编辑课题 " + t.name, text: "编辑", onclick: function () { startEdit(s, t.id); } }));
        acts.appendChild(el("button", { type: "button", "class": "btn btn-ghost", "data-tpkey": "del-" + t.id, "aria-label": "删除课题 " + t.name, text: "删除", onclick: function () { s.confirmDel = t.id; refreshLists(); } }));
      }
      ul.appendChild(el("li", { "class": "tp-item" + (t.enabled ? "" : " is-off") }, [
        el("div", { "class": "tp-itemhead" }, [
          el("span", { "class": "tp-name", text: t.name }),
          el("span", { "class": "tp-count mono", text: n + " 篇命中" }),
          el("label", { "class": "tp-sw" }, [
            switchCtl(t.enabled, "启用课题 " + t.name, "sw-" + t.id, function (v) { T().setEnabled(t.id, v); }),
            el("span", { "class": "tp-swtxt", text: t.enabled ? "启用" : "停用" })
          ])
        ]),
        termChips(t.terms),
        acts
      ]));
    });
    box.appendChild(ul);
  }

  function renderPresets(s) {
    var box = s.presetBox; clear(box);
    var presets = T().getPresets();
    if (!presets.length) { box.hidden = true; return; }
    box.hidden = false;
    box.appendChild(el("h3", { "class": "tp-h", id: "tp-h-preset", text: "预设课题" }));
    box.appendChild(el("p", { "class": "tp-note", text: "站点提供的示例课题，可以直接启用；词项不能在这里改，想改就自己新建一个。" }));
    var ul = el("ul", { "class": "tp-list" });
    presets.forEach(function (t) {
      var n = T().countMatches(s.papers, t.terms);
      ul.appendChild(el("li", { "class": "tp-item" + (t.enabled ? "" : " is-off") }, [
        el("div", { "class": "tp-itemhead" }, [
          el("span", { "class": "tp-name", text: t.name }),
          el("span", { "class": "tp-count mono", text: n + " 篇命中" }),
          el("label", { "class": "tp-sw" }, [
            switchCtl(t.enabled, "启用预设 " + t.name, "psw-" + t.id, function (v) { T().setPresetEnabled(t.id, v); }),
            el("span", { "class": "tp-swtxt", text: t.enabled ? "启用" : "停用" })
          ])
        ]),
        termChips(t.terms)
      ]));
    });
    box.appendChild(ul);
  }

  /* ---------------- 新建 / 编辑表单 ---------------- */

  function startEdit(s, id) {
    s.editing = { id: id };
    renderForm(s);
    if (s.f && s.f.name) { try { s.f.name.focus(); } catch (e) { /* ignore */ } }
    try { s.formBox.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" }); } catch (e) { /* ignore */ }
  }

  function renderForm(s) {
    var box = s.formBox; clear(box); s.f = null;
    var L = T().LIMITS;
    if (!s.editing) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    var cur = null;
    if (s.editing.id) {
      cur = T().allTopics().filter(function (t) { return t.id === s.editing.id && t.kind === "user"; })[0] || null;
      if (!cur) { s.editing = null; box.hidden = true; return; }
    }
    box.appendChild(el("h3", { "class": "tp-h", id: "tp-h-form", text: cur ? "编辑课题" : "新建课题" }));

    var name = el("input", { type: "text", "class": "tp-input", id: "tp-f-name", maxlength: L.nameMax, autocomplete: "off", spellcheck: "false", placeholder: "例如：单细胞扰动预测" });
    var terms = el("textarea", { "class": "tp-input tp-textarea", id: "tp-f-terms", rows: "5", autocomplete: "off", spellcheck: "false", placeholder: "每行一个词项，或用逗号分隔\nperturbation prediction\nvirtual cell\nscRNA*" });
    var live = el("p", { "class": "tp-live mono", role: "status", "aria-live": "polite" });
    var err = el("p", { "class": "tp-err", role: "alert" });
    if (cur) { name.value = cur.name; terms.value = cur.terms.join("\n"); }

    var save = el("button", { type: "button", "class": "btn btn-primary", text: cur ? "保存修改" : "添加课题" });
    var cancel = el("button", { type: "button", "class": "btn btn-ghost", text: "取消", onclick: function () { s.editing = null; renderForm(s); try { var b = s.dlg.querySelector('[data-tpkey="new"]'); if (b) b.focus(); } catch (e) { /* ignore */ } } });

    s.f = { name: name, terms: terms, live: live, err: err };

    box.appendChild(el("div", { "class": "tp-form" }, [
      el("div", { "class": "field" }, [el("label", { "for": "tp-f-name", text: "课题名称（最多 " + L.nameMax + " 字）" }), name]),
      el("div", { "class": "field" }, [el("label", { "for": "tp-f-terms", text: "词项（最多 " + L.maxTerms + " 个，每个 " + L.termMin + "–" + L.termMax + " 字）" }), terms]),
      live, err,
      el("div", { "class": "tp-actions" }, [save, cancel]),
      el("details", { "class": "tp-help" }, [
        el("summary", { text: "词项怎么写" }),
        el("ul", {}, [
          el("li", { text: "不区分大小写；空格与连字符等价，“cell painting” 也匹配 “cell-painting”。" }),
          el("li", { text: "自动匹配复数，“graph” 也匹配 “graphs”；全大写缩写（如 GNN）不加复数。" }),
          el("li", { text: "结尾加 * 表示前缀匹配，“transcript*” 匹配 transcriptome、transcriptomic。" }),
          el("li", { text: "中文按字面匹配，区分大小写与字形，例如 “扰动”。" }),
          el("li", { text: "整个词必须独立出现：“GAN” 不会命中 “organ”。" })
        ])
      ])
    ]));

    function update(immediate) {
      clearTimeout(s.debounce);
      var run = function () {
        if (state !== s || !s.f || s.f.name !== name) return;
        var list = parseTerms(terms.value);
        var v = T().validateTopic({ name: name.value, terms: list });
        var n = v.topic ? T().countMatches(s.papers, v.topic.terms) : 0;
        live.textContent = v.topic ? ("当前命中 " + n + " / " + s.papers.length + " 篇论文") : "";
        err.textContent = v.errors.length && (name.value.trim() || list.length) ? v.errors.join("；") : "";
      };
      if (immediate) run(); else s.debounce = setTimeout(run, 200);
    }
    name.addEventListener("input", function () { update(false); });
    terms.addEventListener("input", function () { update(false); });
    update(true);

    save.addEventListener("click", function () {
      var payload = { name: name.value, terms: parseTerms(terms.value) };
      var r = cur ? T().updateTopic(cur.id, payload) : T().addTopic(payload);
      if (!r.ok) {
        err.textContent = (r.errors && r.errors.length ? r.errors : ["无法保存，请检查名称与词项"]).join("；");
        return;
      }
      toast(cur ? "已保存「" + r.topic.name + "」" : "已添加「" + r.topic.name + "」");
      s.editing = null;
      renderForm(s);
      refreshLists();
      try { var b = s.dlg.querySelector('[data-tpkey="new"]'); if (b) b.focus(); } catch (e) { /* ignore */ }
    });
  }

  /* ---------------- 导出 / 导入 ---------------- */

  function buildShareSection(s) {
    var sec = el("section", { "class": "tp-sec", "aria-labelledby": "tp-h-share" });
    sec.appendChild(el("h3", { "class": "tp-h", id: "tp-h-share", text: "导出与导入" }));
    s.linkBox = el("div", { "class": "tp-linkbox" });
    s.status = el("p", { "class": "tp-err", role: "alert" });
    var fileInp = el("input", { type: "file", accept: ".json,application/json", "class": "tp-file", tabindex: "-1", "aria-hidden": "true" });
    fileInp.addEventListener("change", function () {
      var f = fileInp.files && fileInp.files[0];
      fileInp.value = "";
      if (!f) return;
      if (f.size > 200000) { s.status.textContent = "文件过大（超过 200 KB），不像是课题文件。"; return; }
      readFile(f, function (text) {
        var r = T().importJSON(text);
        if (!r.ok || !r.topics.length) { s.status.textContent = "导入失败：" + ((r.errors && r.errors.length) ? r.errors.slice(0, 3).join("；") : "文件里没有可用的课题"); return; }
        s.status.textContent = "";
        confirmImport(r.topics, {});
      }, function () { s.status.textContent = "读取文件失败。"; });
    });
    sec.appendChild(el("div", { "class": "tp-actions" }, [
      el("button", { type: "button", "class": "btn", text: "复制分享链接", onclick: function () { copyLink(s); } }),
      el("button", { type: "button", "class": "btn", text: "下载 aivc-topics.json", onclick: function () { downloadJSON(s); } }),
      el("button", { type: "button", "class": "btn", text: "从文件导入", onclick: function () { s.status.textContent = ""; fileInp.click(); } }),
      fileInp
    ]));
    sec.appendChild(s.linkBox);
    sec.appendChild(s.status);
    return sec;
  }

  function readFile(file, ok, fail) {
    try {
      var fr = new FileReader();
      fr.onload = function () { ok(String(fr.result || "")); };
      fr.onerror = fail;
      fr.readAsText(file);
    } catch (e) { fail(); }
  }

  function userTopicsCount() { return T().allTopics().filter(function (t) { return t.kind === "user"; }).length; }

  function copyLink(s) {
    clear(s.linkBox); s.status.textContent = "";
    if (!userTopicsCount()) { s.status.textContent = "还没有自己的课题可分享。"; return; }
    var r = T().encodeShare();
    if (r.tooLong) { s.status.textContent = "课题太多，分享链接超过 " + T().LIMITS.maxShareHash + " 个字符，请改用“下载 aivc-topics.json”再发文件。"; return; }
    var url = String(root.location.href).split("#")[0] + r.hash;
    function manual() {
      var inp = el("input", { type: "text", "class": "tp-input tp-linkinp", readonly: true, "aria-label": "分享链接，可手动复制", value: url });
      s.linkBox.appendChild(inp);
      try { inp.focus(); inp.select(); } catch (e) { /* ignore */ }
      toast("请手动复制下面的链接");
    }
    try {
      if (root.navigator && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(function () { toast("分享链接已复制"); }, manual);
      } else manual();
    } catch (e) { manual(); }
  }

  function downloadJSON(s) {
    s.status.textContent = "";
    if (!userTopicsCount()) { s.status.textContent = "还没有自己的课题可导出。"; return; }
    try {
      var blob = new Blob([T().exportJSON()], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url; a.download = "aivc-topics.json";
      document.body.appendChild(a); a.click();
      setTimeout(function () { if (a.parentNode) a.parentNode.removeChild(a); URL.revokeObjectURL(url); }, 0);
      toast("已下载 aivc-topics.json");
    } catch (e) { s.status.textContent = "下载失败，请改用复制分享链接。"; }
  }

  /* ------------------------------------------------------------------ */
  /* 导入确认框                                                           */
  /* ------------------------------------------------------------------ */

  function confirmImport(topics, opts) {
    opts = opts || {};
    if (!T() || !A()) return;
    var list = Array.isArray(topics) ? topics.filter(function (t) { return t && typeof t.name === "string" && Array.isArray(t.terms); }) : [];
    function done(v) { if (typeof opts.onDone === "function") { try { opts.onDone(v); } catch (e) { /* ignore */ } } }
    if (!list.length) { toast("没有可导入的课题"); done(null); return; }
    if (confirmState) { try { confirmState.close(); } catch (e) { /* ignore */ } }

    var prevFocus = document.activeElement;
    var dlg = makeDialog("tp-confirm", "确认导入课题");
    var confirmed = false;
    var ul = el("ul", { "class": "tp-list tp-implist" });
    list.forEach(function (t) {
      ul.appendChild(el("li", { "class": "tp-item" }, [
        el("div", { "class": "tp-itemhead" }, [el("span", { "class": "tp-name", text: t.name })]),
        termChips(t.terms)
      ]));
    });
    var cancel = el("button", { type: "button", "class": "btn", text: "取消", onclick: function () { closeDialog(dlg); } });
    var merge = el("button", { type: "button", "class": "btn btn-primary", text: "合并到我的课题", onclick: function () {
      confirmed = true;
      var added = T().mergeImported(list);
      toast(added > 0 ? "已加入 " + added + " 个课题" + (added < list.length ? "（其余同名或超出上限，已跳过）" : "") : "没有加入新课题（同名或已达上限）");
      closeDialog(dlg);
      done(added);
    } });
    dlg.appendChild(el("div", { "class": "tp-in" }, [
      el("h2", { "class": "tp-title", text: "导入 " + list.length + " 个课题？" }),
      el("p", { "class": "tp-note", text: "这些内容来自分享链接或文件，请确认后再合并。合并不会覆盖同名课题。" }),
      ul,
      el("div", { "class": "tp-actions tp-confirm-actions" }, [cancel, merge])
    ]));
    document.body.appendChild(dlg);
    confirmState = dlg;
    dlg.addEventListener("close", function () {
      if (dlg.parentNode) dlg.parentNode.removeChild(dlg);
      if (confirmState === dlg) confirmState = null;
      if (!confirmed) done(null);
      if (prevFocus && prevFocus.isConnected && typeof prevFocus.focus === "function") { try { prevFocus.focus(); } catch (e) { /* ignore */ } }
    });
    try { dlg.showModal(); } catch (e) { dlg.setAttribute("open", ""); }
    try { cancel.focus(); } catch (e) { /* ignore */ }
  }

  root.AIVCTopicsUI = { open: open, close: close, isOpen: isOpen, confirmImport: confirmImport };
})(typeof window !== "undefined" ? window : this);
