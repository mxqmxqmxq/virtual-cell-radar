#!/usr/bin/env node
// 截图 + 断言：通过 Chrome DevTools Protocol 驱动 headless Chrome（纯 node，无 npm 依赖）。
// 用法：node scripts/site_shots.mjs [baseUrl=http://127.0.0.1:8010] [outDir=.shots] [prefix=""]
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const BASE = (process.argv[2] || "http://127.0.0.1:8010").replace(/\/$/, "");
const OUT = resolve(process.argv[3] || ".shots");
const PREFIX = process.argv[4] ? process.argv[4] + "_" : "";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9300 + Math.floor(Math.random() * 500);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
mkdirSync(OUT, { recursive: true });

const profile = mkdtempSync(join(tmpdir(), "aivc-chrome-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "--disable-gpu", "about:blank"], { stdio: "ignore" });

let ws, nextId = 1; const pending = new Map();
function send(method, params = {}, sessionId) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params, sessionId }));
  return new Promise((res, rej) => pending.set(id, { res, rej, method }));
}
async function connect() {
  for (let i = 0; i < 60; i++) {
    try {
      const j = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
      ws = new WebSocket(j.webSocketDebuggerUrl);
      await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.id && pending.has(m.id)) {
          const p = pending.get(m.id); pending.delete(m.id);
          m.error ? p.rej(new Error(p.method + ": " + m.error.message)) : p.res(m.result);
        }
      };
      return;
    } catch { await sleep(250); }
  }
  throw new Error("Chrome 未启动");
}

let results = [], failed = 0;
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  if (!ok) failed++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`);
}

async function newPage({ w, h, scheme, reduced }) {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const S = (m, p) => send(m, p, sessionId);
  await S("Page.enable");
  await S("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
  await S("Emulation.setEmulatedMedia", { features: [
    { name: "prefers-color-scheme", value: scheme },
    { name: "prefers-reduced-motion", value: reduced ? "reduce" : "no-preference" }] });
  const ev = async (expr) => {
    const r = await S("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("eval: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  };
  const shot = async (file, full) => {
    let params = { format: "png" };
    if (full) {
      const dims = await ev("({w:document.documentElement.scrollWidth,h:Math.min(document.documentElement.scrollHeight,12000)})");
      params = { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: w, height: dims.h, scale: 1 } };
    }
    const r = await S("Page.captureScreenshot", params);
    writeFileSync(join(OUT, file), Buffer.from(r.data, "base64"));
    return join(OUT, file);
  };
  const goto = async (url) => {
    await S("Page.navigate", { url });
    for (let i = 0; i < 80; i++) {
      await sleep(250);
      try { if (await ev(`document.documentElement.dataset.ready==="1"`)) return true; } catch { /* loading */ }
    }
    return false;
  };
  const close = () => send("Target.closeTarget", { targetId });
  return { ev, shot, goto, close, S };
}

async function scrollThrough(p) {
  const H = await p.ev("document.documentElement.scrollHeight");
  const vh = await p.ev("innerHeight");
  for (let y = 0; y < H; y += Math.round(vh * 0.8)) {
    await p.ev(`window.scrollTo(0,${y})`); await sleep(600);
  }
  await p.ev(`window.scrollTo(0,document.documentElement.scrollHeight)`); await sleep(700);
}

const baseState = `({ready:document.documentElement.dataset.ready, err:document.documentElement.dataset.jsError,
  sw:document.documentElement.scrollWidth, iw:window.innerWidth, anim:document.getAnimations().length,
  inf:document.getAnimations().filter(a=>{try{return a.effect.getComputedTiming().iterations===Infinity}catch(e){return false}}).length})`;

async function homeGroup(w, h, scheme) {
  const tag = `${w}x${h}_${scheme}`;
  const p = await newPage({ w, h, scheme, reduced: false });
  const ok = await p.goto(`${BASE}/#/`);
  check(`[home ${tag}] ready`, ok);
  await sleep(2500);
  const st = await p.ev(baseState);
  check(`[home ${tag}] 无 jsError`, st.err === undefined, String(st.err ?? ""));
  check(`[home ${tag}] 无横向滚动`, st.sw <= st.iw, `${st.sw}<=${st.iw}`);
  check(`[home ${tag}] 动画在跑`, st.anim > 0, `getAnimations=${st.anim}`);
  const isHome = await p.ev(`!!document.querySelector(".hm-home")`);
  check(`[home ${tag}] 使用 AIVCHome 首页`, isHome);
  await p.shot(`${PREFIX}home_${tag}_fold.png`, false);
  if (w >= 1000) {
    check(`[home ${tag}] 顶部 data-over=dark`, await p.ev(`document.getElementById("topbar").dataset.over==="dark"`));
  }
  await scrollThrough(p);
  const st2 = await p.ev(baseState);
  check(`[home ${tag}] 滚动后无 jsError / 横向滚动`, st2.err === undefined && st2.sw <= st2.iw, String(st2.err ?? ""));
  if (w >= 1000) check(`[home ${tag}] 滚过首屏后 data-over 消失`, await p.ev(`document.getElementById("topbar").dataset.over===undefined`));
  await p.ev(`window.scrollTo(0,0)`); await sleep(500);
  if (w >= 1000) check(`[home ${tag}] 回顶后 data-over=dark`, await p.ev(`document.getElementById("topbar").dataset.over==="dark"`));
  // 整页图：先滚到底触发所有进入动效再截
  await scrollThrough(p); await sleep(1200);
  const f = await p.shot(`${PREFIX}home_${tag}_full.png`, true);
  console.log("  shot", f);
  // 首页 -> 论文流 -> 首页
  await p.ev(`location.hash="#/papers"`); await sleep(900);
  await p.ev(`location.hash="#/"`); await sleep(1500);
  const st3 = await p.ev(baseState);
  check(`[home ${tag}] 首页<->论文流来回无报错`, st3.err === undefined, String(st3.err ?? ""));
  await p.close();
}

async function reducedGroup() {
  const p = await newPage({ w: 1280, h: 800, scheme: "light", reduced: true });
  const ok = await p.goto(`${BASE}/#/`);
  check(`[home reduced] ready`, ok);
  await sleep(1500);
  await scrollThrough(p);
  const st = await p.ev(baseState);
  check(`[home reduced] 无 jsError`, st.err === undefined, String(st.err ?? ""));
  check(`[home reduced] 无横向滚动`, st.sw <= st.iw);
  check(`[home reduced] 无无限循环动画`, st.inf === 0, `infinite=${st.inf}`);
  const hidden = await p.ev(`(function(){var n=0;document.querySelectorAll(".hm-home *, .dm-section *").forEach(function(e){var c=getComputedStyle(e);if(c.opacity==="0"&&c.visibility!=="hidden"&&e.getBoundingClientRect().width>0&&!e.matches("[aria-hidden='true'], [aria-hidden='true'] *")&&!e.className.toString().match(/sr|measure|glow/))n++});return n})()`);
  check(`[home reduced] 无 opacity:0 的残留元素`, hidden === 0, `count=${hidden}`);
  const kpi = await p.ev(`Array.from(document.querySelectorAll(".hm-kpi-n")).map(e=>e.textContent.trim()).join(",")`);
  check(`[home reduced] KPI 为终值`, kpi.length > 0 && !/^0(,0)*$/.test(kpi), kpi);
  const cells = await p.ev(`(function(){var c=document.querySelectorAll(".dg-pulse .hm-heat .hm-cell");var z=0;c.forEach(function(e){if(getComputedStyle(e).opacity!=="1")z++});return [c.length,z]})()`);
  check(`[home reduced] 热力图格子 opacity=1`, cells[1] === 0, `cells=${cells[0]} notOne=${cells[1]}`);
  await p.shot(`${PREFIX}home_1280x800_light_reduced_full.png`, true);
  await p.close();
}

const ROUTES = ["#/papers", "#/papers?sort=final", "#/map", "#/datasets", "#/weekly", "#/news", "#/news?view=funding", "#/news?view=companies", "#/about", "#/resources"];
async function routesGroup(w, h, scheme = "light") {
  const p = await newPage({ w, h, scheme, reduced: false });
  for (const r of ROUTES) {
    const name = r.replace(/[#/?=]+/g, "_").replace(/^_|_$/g, "");
    await p.goto(`${BASE}/#/`); await sleep(300);
    await p.ev(`location.hash=${JSON.stringify(r)}`); await sleep(1400);
    await scrollThrough(p); await p.ev(`window.scrollTo(0,0)`); await sleep(400);
    const st = await p.ev(baseState);
    const tg = scheme === "dark" ? " dark" : "";
    check(`[route ${r} ${w}${tg}] 无 jsError`, st.err === undefined && st.ready === "1", String(st.err ?? ""));
    check(`[route ${r} ${w}${tg}] 无横向滚动`, st.sw <= st.iw, `${st.sw}<=${st.iw}`);
    await p.shot(`${PREFIX}route_${w}${scheme === "dark" ? "_dark" : ""}_${name}.png`, false);
  }
  await p.close();
}

// ---- 集成断言：模块挂载 / 导航换肤 / 标签页 / reduced / 路由往返不累积 ----
const PROBE = `(function(){
  var P = window.__probe = { iv: new Set(), raf: new Set(), to: 0 };
  var si = window.setInterval, ci = window.clearInterval, raf = window.requestAnimationFrame, caf = window.cancelAnimationFrame;
  window.setInterval = function(f, t){ var id = si.apply(window, arguments); P.iv.add(id); return id; };
  window.clearInterval = function(id){ P.iv.delete(id); return ci.call(window, id); };
  window.requestAnimationFrame = function(f){ var id = raf.call(window, function(t){ P.raf.delete(id); f(t); }); P.raf.add(id); return id; };
  window.cancelAnimationFrame = function(id){ P.raf.delete(id); return caf.call(window, id); };
})();`;

async function integrationGroup() {
  const p = await newPage({ w: 1280, h: 800, scheme: "light", reduced: false });
  await p.S("Page.addScriptToEvaluateOnNewDocument", { source: PROBE });
  const ok = await p.goto(`${BASE}/#/`);
  check(`[integ] ready`, ok);
  await sleep(2500);
  const info = await p.ev(`(function(){
    var h = document.querySelector(".hm-home");
    var seq = Array.from(h.querySelectorAll(":scope > .hm-hero, :scope .hm-sec, :scope > .hm-cta"))
      .map(function(e){ return e.className.toString().split(" ").filter(function(c){return /^(hm-hero|dg|dg-pulse|hm-how|hm-cta)$/.test(c)})[0]; });
    return { seq: seq.join(">"),
      news: !!h.querySelector(".dg-side a[href='#/news']"),
      canvas: document.querySelectorAll("canvas.gl-canvas").length, mods: ["AIVCGlow","AIVCDemos","AIVCSteps","AIVCTerm","AIVCHome","AIVCDigest"].filter(function(k){return !!window[k]}).length,
      old: !!h.querySelector(".st-outer, .tm-section, .fq-section, .hm-featured, .hm-datasets, .hm-weekly"),
      side: h.querySelectorAll(".dg-side > .dg-blk").length, papers: h.querySelectorAll(".dg-papers .dg-row").length,
      err: document.documentElement.dataset.jsError };
  })()`);
  check(`[integ] 首页区块顺序（恰为 5 块）`, info.seq === "hm-hero>dg>dg-pulse>hm-how>hm-cta", info.seq);
  check(`[integ] 速览区含论文 + 三段右栏，且有到 #/news 的入口`, info.papers > 0 && info.side === 3 && info.news, JSON.stringify(info));
  check(`[integ] 旧区块（分步/终端/FAQ/精选/数据集/周报）已不在首页`, !info.old);
  check(`[integ] 光晕 canvas 已挂载`, info.canvas >= 1, `canvas=${info.canvas}`);
  check(`[integ] 6 个模块全局对象存在且无 jsError`, info.mods === 6 && info.err === undefined, `mods=${info.mods} err=${info.err}`);

  // 标签页演示可切换
  await p.ev(`document.querySelector(".dm-section").scrollIntoView({block:"center"})`); await sleep(1200);
  const before = await p.ev(`Array.from(document.querySelectorAll(".dm-tab")).map(function(b){return b.getAttribute("aria-pressed")}).join()`);
  await p.ev(`document.querySelectorAll(".dm-tab")[2].click()`); await sleep(1400);
  const after = await p.ev(`Array.from(document.querySelectorAll(".dm-tab")).map(function(b){return b.getAttribute("aria-pressed")}).join()`);
  check(`[integ] 标签页演示可切换`, before.startsWith("true") && after === "false,false,true,false", `${before} -> ${after}`);

  // 路由往返 3 次不累积
  await p.ev(`window.scrollTo(0,0)`); await sleep(600);
  const snap = () => p.ev(`({anim: document.getAnimations().length, iv: window.__probe.iv.size, raf: window.__probe.raf.size,
    nodes: document.querySelectorAll("*").length, canvas: document.querySelectorAll("canvas").length})`);
  await p.ev(`location.hash="#/papers"`); await sleep(1200);
  await p.ev(`location.hash="#/"`); await sleep(2500);
  const s0 = await snap();
  for (let i = 0; i < 3; i++) {
    await p.ev(`location.hash="#/papers"`); await sleep(1000);
    await p.ev(`location.hash="#/"`); await sleep(2000);
  }
  await sleep(1500);
  const s1 = await snap();
  check(`[integ] 往返 3 次：getAnimations 不累积`, s1.anim <= s0.anim + 2, `${s0.anim} -> ${s1.anim}`);
  check(`[integ] 往返 3 次：活动 setInterval 不累积`, s1.iv <= s0.iv, `${s0.iv} -> ${s1.iv}`);
  check(`[integ] 往返 3 次：待执行 rAF 不累积`, s1.raf <= s0.raf + 1, `${s0.raf} -> ${s1.raf}`);
  check(`[integ] 往返 3 次：canvas / DOM 节点数稳定`, s1.canvas === s0.canvas && Math.abs(s1.nodes - s0.nodes) < 40, `canvas ${s0.canvas}->${s1.canvas} nodes ${s0.nodes}->${s1.nodes}`);
  // 离开首页后不应留下光晕 canvas 和动画
  await p.ev(`location.hash="#/papers"`); await sleep(1500);
  const away = await p.ev(`({canvas: document.querySelectorAll("canvas").length, raf: window.__probe.raf.size, iv: window.__probe.iv.size})`);
  check(`[integ] 离开首页后无 canvas 残留`, away.canvas === 0, JSON.stringify(away));
  await p.close();
}

// ---- #/about：旅程分步 / 命令行终端 / FAQ 三个模块脱离首页后的行为 ----
async function aboutGroup() {
  const p = await newPage({ w: 1280, h: 800, scheme: "light", reduced: false });
  const ok = await p.goto(`${BASE}/#/about`);
  check(`[about] ready`, ok);
  await sleep(1500);
  const info = await p.ev(`({st: !!document.querySelector(".st-outer, .st-stage"), n: document.querySelectorAll(".st-step").length, tm: !!document.querySelector(".tm-tab"),
    fq: document.querySelectorAll(".fq-item").length, home: !!document.querySelector(".hm-home.hm-about"), foot: !!document.querySelector(".foot a[href='#/about']"), err: document.documentElement.dataset.jsError})`);
  check(`[about] 存在 .st- / .tm- / .fq- 区块且页脚有入口`, info.st && info.tm && info.fq > 0 && info.home && info.foot && info.err === undefined, JSON.stringify(info));
  check(`[about] 分步区块有 5 步`, info.n === 5, String(info.n));
  await p.ev(`window.scrollTo(0, document.querySelector(".tm-section").getBoundingClientRect().top+scrollY-10)`); await sleep(900);
  check(`[about] 终端区 data-over=dark`, await p.ev(`document.getElementById("topbar").dataset.over==="dark"`));
  await p.ev(`window.scrollTo(0, 0)`); await sleep(900); // 关于页 FAQ 在页尾，滚动会被页底截住，改为回顶验证换肤恢复
  check(`[about] 滚出终端区后恢复`, await p.ev(`document.getElementById("topbar").dataset.over===undefined`));
  // FAQ 手风琴互斥
  const fq = await p.ev(`(function(){var d=document.querySelectorAll(".fq-item");if(d.length<2)return null;d[0].open=true;d[1].open=true;return new Promise(function(r){setTimeout(function(){r(Array.from(d).filter(function(x){return x.open}).length)},300)})})()`);
  check(`[about] FAQ 手风琴互斥`, fq === 1, String(fq));
  // 分步：点击第 3 步
  const cur = await p.ev(`(function(){var b=document.querySelectorAll(".st-step-btn");if(b.length<3)return -1;b[2].click();return new Promise(function(r){setTimeout(function(){r(Array.from(document.querySelectorAll(".st-step-btn")).findIndex(function(x){return x.getAttribute("aria-current")==="step"}))},1200)})})()`);
  check(`[about] 点击分步可切换`, cur === 2, String(cur));
  const st = await p.ev(baseState);
  check(`[about] 无 jsError / 横向滚动`, st.err === undefined && st.sw <= st.iw, `${st.sw}<=${st.iw} ${st.err ?? ""}`);
  await p.shot(`${PREFIX}about_1280x800_light_full.png`, true);
  await p.close();
  const q = await newPage({ w: 375, h: 812, scheme: "dark", reduced: false });
  await q.goto(`${BASE}/#/about`); await sleep(1200); await scrollThrough(q);
  const st2 = await q.ev(baseState);
  check(`[about 375 dark] 无 jsError / 横向滚动`, st2.err === undefined && st2.sw <= st2.iw, `${st2.sw}<=${st2.iw} ${st2.err ?? ""}`);
  await q.shot(`${PREFIX}about_375x812_dark_full.png`, true);
  await q.close();
}

// view() 时间线不可用时的 IntersectionObserver 回退路径
async function fallbackGroup() {
  const p = await newPage({ w: 1280, h: 800, scheme: "light", reduced: false });
  await p.S("Page.addScriptToEvaluateOnNewDocument", { source: `(function(){var o=CSS.supports.bind(CSS);CSS.supports=function(a,b){var q=String(a)+(b||"");return /animation-timeline|view\\(/.test(q)?false:o.apply(CSS,arguments)}})();` });
  const ok = await p.goto(`${BASE}/#/`);
  check(`[fallback] ready`, ok);
  await sleep(1500);
  check(`[fallback] 未启用 mo-vt`, await p.ev(`!document.documentElement.classList.contains("mo-vt")`));
  await scrollThrough(p); await sleep(1200);
  const st = await p.ev(baseState);
  check(`[fallback] 无 jsError / 横向滚动`, st.err === undefined && st.sw <= st.iw, String(st.err ?? ""));
  const stuck = await p.ev(`(function(){var n=[];document.querySelectorAll(".mo-rise, .reveal, .hm-rise").forEach(function(e){var r=e.getBoundingClientRect();if(r.width>0&&getComputedStyle(e).opacity==="0"&&!e.closest("[aria-hidden='true']"))n.push(e.className.toString().slice(0,30))});return n.slice(0,5)})()`);
  check(`[fallback] 滚过后入场元素全部显现`, stuck.length === 0, stuck.join(" | "));
  // sticky 分步在回退路径下仍随滚动切换（分步区块已移到 #/about）
  await p.ev(`location.hash="#/about"`); await sleep(1500);
  const o = await p.ev(`(function(){var e=document.querySelector(".st-outer");return e?e.getBoundingClientRect().top+scrollY:null})()`);
  if (o != null) {
    await p.ev(`window.scrollTo(0, ${o} - 88 + 1300)`); await sleep(1200);
    const cur = await p.ev(`Array.from(document.querySelectorAll(".st-step-btn")).findIndex(function(b){return b.getAttribute("aria-current")==="step"})`);
    check(`[fallback] 分步区块随滚动切换（回退路径）`, cur === 1 || cur === 2, `active=${cur}`);
  }
  await p.close();
}

async function reducedIntegGroup() {
  const p = await newPage({ w: 1280, h: 800, scheme: "light", reduced: true });
  const ok = await p.goto(`${BASE}/#/`);
  check(`[integ reduced] ready`, ok);
  await sleep(1200); await scrollThrough(p);
  check(`[integ reduced] DOM 无 canvas`, await p.ev(`document.querySelectorAll("canvas").length`) === 0);
  const st = await p.ev(baseState);
  check(`[integ reduced] 整页无无限动画`, st.inf === 0, `infinite=${st.inf}`);
  const hidden = await p.ev(`(function(){var n=[];document.querySelectorAll("main *, .hm-home *").forEach(function(e){var c=getComputedStyle(e);if(c.opacity==="0"&&c.visibility!=="hidden"&&c.display!=="none"&&e.getBoundingClientRect().width>0&&!e.closest("[aria-hidden='true']")&&!e.matches("[aria-hidden='true'],canvas,.gl-canvas,.fq-minus,.fq-plus"))n.push(e.className.toString().slice(0,30))});return n.slice(0,6)})()`);
  check(`[integ reduced] 全页无 opacity:0 残留`, hidden.length === 0, hidden.join(" | "));
  await p.ev(`location.hash="#/about"`); await sleep(1200); await scrollThrough(p);
  const steps = await p.ev(`(function(){var s=Array.from(document.querySelectorAll(".st-step")),pn=Array.from(document.querySelectorAll(".st-stage"));
    return {n:s.length, p:pn.length, dim:s.filter(function(e){return parseFloat(getComputedStyle(e).opacity)<0.99||parseFloat(getComputedStyle(e.querySelector(".st-step-btn")).opacity)<0.99}).length,
      vis:pn.filter(function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).opacity==="1"&&getComputedStyle(e).visibility!=="hidden"}).length}})()`);
  check(`[integ reduced] 分步区块 5 步全可见`, steps.n === 5 && steps.dim === 0 && steps.vis >= 5, JSON.stringify(steps));
  const tm = await p.ev(`Array.from(document.querySelectorAll(".tm-out")).every(function(e){return getComputedStyle(e).opacity==="1"})`);
  check(`[integ reduced] 终端输出行全部可见`, tm === true);
  await p.close();
}

async function mobileIntegGroup(scheme) {
  const p = await newPage({ w: 375, h: 812, scheme, reduced: false });
  const ok = await p.goto(`${BASE}/#/`);
  check(`[integ 375 ${scheme}] ready`, ok);
  await sleep(1500); await scrollThrough(p);
  const st = await p.ev(baseState);
  check(`[integ 375 ${scheme}] 首页无横向滚动且无 jsError`, st.sw <= st.iw && st.err === undefined, `${st.sw}<=${st.iw} ${st.err ?? ""}`);
  const wide = await p.ev(`Array.from(document.querySelectorAll(".hm-home *")).filter(function(e){var r=e.getBoundingClientRect();if(r.right<=window.innerWidth+1||r.width===0)return false;for(var a=e.parentElement;a&&a!==document.body;a=a.parentElement){var o=getComputedStyle(a).overflowX;if(o!=="visible")return false}return true}).slice(0,3).map(function(e){return e.className.toString().slice(0,30)})`);
  check(`[integ 375 ${scheme}] 首页无元素越界(被 overflow 裁切/滚动容器内的除外)`, wide.length === 0, wide.join(" | "));
  await p.close();
}

// ---- 我的课题：面板 / 标签 / 导入确认 / 首页不重建 / 模块缺失降级 ----
async function topicsGroup() {
  const p = await newPage({ w: 1280, h: 800, scheme: "light", reduced: false });
  let ok = await p.goto(`${BASE}/#/papers`);
  check(`[topics] ready`, ok);
  await p.ev(`localStorage.clear()`);
  await p.ev(`location.hash="#/topics"`); await sleep(1200);
  check(`[topics] #/topics 打开面板且下方是论文流`, await p.ev(`!!document.querySelector("dialog.tp-panel[open]") && !!document.querySelector(".pcard")`));
  check(`[topics] 无启用课题时筛选区是「设置我的课题」按钮`, await p.ev(`!!document.querySelector("#f-topic.f-topic-btn")`));
  await p.ev(`AIVCTopicsUI.close()`); await sleep(900);
  check(`[topics] 关闭面板后地址回到 #/papers`, await p.ev(`!document.querySelector("dialog.tp-panel") && location.hash.indexOf("#/papers")===0`), await p.ev(`location.hash`));
  await p.ev(`window.scrollTo(0,300)`);
  await p.ev(`AIVCTopics.addTopic({name:"测试课题",terms:["single-cell","perturbation","cell"],enabled:true})`); await sleep(700);
  const t1 = await p.ev(`({hl:Array.from(document.querySelectorAll(".pcard .tag-hl")).map(function(e){return e.textContent}).slice(0,3), nav:document.getElementById("nav-topics-n").textContent, sel:!!document.querySelector("select#f-topic"), y:window.scrollY, err:document.documentElement.dataset.jsError})`);
  check(`[topics] 添加课题后卡片出现「课题 · 」标签`, t1.hl.length > 0 && t1.hl.every((x) => x.startsWith("课题 · ")), JSON.stringify(t1.hl));
  check(`[topics] 导航计数=1 且筛选区变为下拉、滚动位置保持`, t1.nav === "1" && t1.sel && t1.y === 300 && t1.err === undefined, JSON.stringify(t1));
  await p.ev(`(function(){var s=document.getElementById("f-topic");s.value="any";s.dispatchEvent(new Event("change"))})()`); await sleep(500);
  check(`[topics] 课题筛选写入地址`, await p.ev(`location.hash.indexOf("topic=any")>0`));
  await p.ev(`Array.from(document.querySelectorAll(".btn")).find(function(b){return b.textContent==="清除筛选"}).click()`); await sleep(300);
  check(`[topics] 清除筛选同时清掉 topic`, await p.ev(`location.hash.indexOf("topic")<0 && document.getElementById("f-topic").value===""`));
  await p.ev(`location.hash="#/papers?topic=nope"`); await sleep(900);
  check(`[topics] 不存在的课题 id：不报错且有可见提示`, await p.ev(`!document.documentElement.dataset.jsError && /不存在或未启用/.test(document.querySelector(".results").textContent)`));
  // 导入：不点确认就不生效，地址被清理
  const before = await p.ev(`localStorage.getItem("aivc.topics.v1")`);
  const hash = await p.ev(`AIVCTopics.encodeShare([{name:"导入课题",terms:["transformer"]}]).hash`);
  await p.ev(`location.hash=${JSON.stringify(hash)}`); await sleep(1200);
  const imp = await p.ev(`({dlg:!!document.querySelector("dialog.tp-confirm[open]"), hash:location.hash, ls:localStorage.getItem("aivc.topics.v1"), err:document.documentElement.dataset.jsError})`);
  check(`[topics] 导入链接弹出确认框`, imp.dlg);
  check(`[topics] 导入载荷已从地址去掉`, imp.hash.indexOf("import")<0, imp.hash);
  check(`[topics] 未确认时 localStorage 无变化`, imp.ls === before && imp.err === undefined);
  await p.ev(`Array.from(document.querySelectorAll("dialog.tp-confirm button")).find(function(b){return b.textContent==="取消"}).click()`); await sleep(700);
  check(`[topics] 取消后仍无变化`, await p.ev(`localStorage.getItem("aivc.topics.v1")`) === before);
  await p.ev(`location.hash="#/topics?import=!!bad"`); await sleep(900);
  check(`[topics] 无效载荷：toast「导入链接无效」且地址被清理`, await p.ev(`/导入链接无效/.test(document.body.textContent) && location.hash.indexOf("import")<0`));
  // 周报页
  await p.ev(`location.hash="#/weekly"`); await sleep(1000);
  check(`[topics] 周报页有「本周与我的课题相关」`, await p.ev(`!!document.querySelector(".wk-topics") && !document.documentElement.dataset.jsError`));
  // 首页：增删课题不重建
  await p.ev(`location.hash="#/"`); await sleep(2500);
  await p.ev(`window.__homeNode=document.querySelector(".hm-home")`);
  const cv0 = await p.ev(`document.querySelectorAll("canvas").length`);
  await p.ev(`(function(){var r=AIVCTopics.addTopic({name:"另一个",terms:["transformer"]});AIVCTopics.removeTopic(r.topic.id)})()`); await sleep(800);
  const c1 = await p.ev(`({same:window.__homeNode===document.querySelector(".hm-home"), canvas:document.querySelectorAll("canvas").length, anim:document.getAnimations().length, err:document.documentElement.dataset.jsError})`);
  check(`[topics] 首页增删课题不重建（节点同一、canvas 数不变）`, c1.same && c1.err === undefined && c1.canvas === cv0, JSON.stringify(c1));
  await p.close();

  // 模块缺失：屏蔽 topics.js / topics-ui.js
  const q = await newPage({ w: 1280, h: 800, scheme: "light", reduced: false });
  await q.S("Network.enable");
  await q.S("Network.setBlockedURLs", { urls: ["*assets/topics.js", "*assets/topics-ui.js"] });
  ok = await q.goto(`${BASE}/#/papers`);
  check(`[topics-missing] ready`, ok);
  await sleep(600);
  const m = await q.ev(`({err:document.documentElement.dataset.jsError, cards:document.querySelectorAll(".pcard").length, nav:document.getElementById("nav-topics").hidden, filter:!!document.querySelector("#f-topic")})`);
  check(`[topics-missing] 论文流正常、无 jsError、无课题入口`, m.err === undefined && m.cards > 0 && m.nav && !m.filter, JSON.stringify(m));
  await q.ev(`location.hash="#/topics"`); await sleep(900);
  check(`[topics-missing] #/topics 降级回 #/papers 且无报错`, await q.ev(`location.hash.indexOf("#/papers")===0 && !document.documentElement.dataset.jsError`));
  await q.ev(`location.hash="#/weekly"`); await sleep(800);
  check(`[topics-missing] 周报页正常`, await q.ev(`!document.querySelector(".wk-topics") && !document.documentElement.dataset.jsError && !!document.querySelector(".wk")`));
  await q.close();
}

// ---- 知识资源页：卡片 / 筛选同步地址 / 搜索 / 首页入口 / 互链 ----
async function resourcesGroup(w, h, scheme) {
  const tg = ` ${w}${scheme === "dark" ? " dark" : ""}`;
  const p = await newPage({ w, h, scheme, reduced: false });
  await p.goto(`${BASE}/#/resources`); await sleep(1500);
  const a = await p.ev(`({cards:document.querySelectorAll(".rs-card").length, groups:document.querySelectorAll(".rs-group").length,
    gh:document.querySelectorAll(".rs-gh .rs-star").length, sw:document.documentElement.scrollWidth, iw:window.innerWidth, err:document.documentElement.dataset.jsError})`);
  check(`[resources${tg}] 有分组卡片、仓库指标，无 jsError / 横向滚动`, a.cards > 0 && a.groups > 1 && a.gh > 0 && a.sw <= a.iw && a.err === undefined, JSON.stringify(a));
  await p.ev(`(function(){var s=document.getElementById("rs-f-kind");s.value="repo";s.dispatchEvent(new Event("change"))})()`); await sleep(300);
  const b = await p.ev(`({h:location.hash, groups:document.querySelectorAll(".rs-group").length, cards:document.querySelectorAll(".rs-card").length})`);
  check(`[resources${tg}] 类型筛选同步到地址`, /kind=repo/.test(b.h) && b.groups === 1 && b.cards > 0, JSON.stringify(b));
  await p.ev(`(function(){var i=document.getElementById("rs-f-q");i.value="zzzz-no-such";i.dispatchEvent(new Event("input"))})()`); await sleep(500);
  check(`[resources${tg}] 搜索无结果给空状态`, await p.ev(`!!document.querySelector(".rs-results .empty") && /q=/.test(location.hash)`));
  await p.ev(`document.querySelector(".rs-results .empty button").click()`); await sleep(300);
  check(`[resources${tg}] 清除筛选恢复列表`, await p.ev(`document.querySelectorAll(".rs-card").length>1 && location.hash==="#/resources"`));
  await p.goto(`${BASE}/#/resources?id=scgpt`); await sleep(1500);
  check(`[resources${tg}] ?id= 定位并高亮`, await p.ev(`!!document.querySelector(".rs-card.is-target")`));
  await p.shot(`${PREFIX}resources_${w}${scheme === "dark" ? "_dark" : ""}.png`, false);
  await p.goto(`${BASE}/#/`); await sleep(1500);
  const c = await p.ev(`(function(){var a=document.querySelector(".hm-cta-row a[href='#/resources']");return {has:!!a, secs:document.querySelectorAll(".hm-home > section, .hm-home > .hm-rails").length, sw:document.documentElement.scrollWidth, iw:window.innerWidth}})()`);
  check(`[resources${tg}] 首页结尾区有资源入口且无横向滚动`, c.has && c.sw <= c.iw, JSON.stringify(c));
  await p.close();
}

try {
  await connect();
  const ONLY = process.env.ONLY_INTEG === "1"; // 调试用：只跑集成断言
  if (!ONLY) {
    for (const [w, h] of [[1280, 800], [375, 812]]) for (const s of ["light", "dark"]) await homeGroup(w, h, s);
    await reducedGroup();
    await routesGroup(1280, 800);
    await routesGroup(375, 812);
    await routesGroup(375, 812, "dark");
    await routesGroup(1280, 800, "dark");
  }
  await integrationGroup();
  await aboutGroup();
  await fallbackGroup();
  await reducedIntegGroup();
  await mobileIntegGroup("light");
  await mobileIntegGroup("dark");
  await topicsGroup();
  for (const [w, h] of [[1280, 800], [375, 812]]) for (const sc of ["light", "dark"]) await resourcesGroup(w, h, sc);
} catch (e) {
  check("脚本运行", false, e.message);
} finally {
  try { ws && ws.close(); } catch {}
  chrome.kill("SIGTERM"); await sleep(500); try { chrome.kill("SIGKILL"); } catch {}
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
}
console.log(`\n${results.length - failed}/${results.length} 通过，${failed} 失败；截图目录 ${OUT}`);
process.exit(failed ? 1 : 0);
