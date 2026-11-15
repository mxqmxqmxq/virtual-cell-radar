/* home-glow：首屏/深色区光晕背景。2D canvas 低频漂移色斑；无库、无指针响应。 */
(function () {
  "use strict";
  var FRAME_MS = 1000 / 30;
  var stats = { frames: 0, running: 0 };

  function mount(container, api) {
    if (!container) return null;
    var motion = (api && api.motion) || {};
    var cleanups = [];
    function reg(fn) {
      if (typeof motion.onCleanup === "function") motion.onCleanup(fn);
      cleanups.push(fn);
    }
    function dispose() { var l = cleanups; cleanups = []; l.forEach(function (f) { try { f(); } catch (e) { /* ignore */ } }); }

    var fallback = document.createElement("div");
    fallback.className = "gl-fallback";
    fallback.setAttribute("aria-hidden", "true");
    container.appendChild(fallback);
    if (motion.reduced) return { dispose: dispose };

    var canvas, ctx;
    try {
      canvas = document.createElement("canvas");
      ctx = canvas.getContext("2d");
    } catch (e) { ctx = null; }
    if (!ctx) return { dispose: dispose };
    canvas.className = "gl-canvas";
    canvas.setAttribute("aria-hidden", "true");
    container.appendChild(canvas);

    var W = 0, H = 0, raf = 0, last = 0, inView = true, shown = false, dead = false;
    var colors = [];
    var probe = document.createElement("span");
    probe.style.cssText = "position:absolute;width:0;height:0;visibility:hidden";
    container.appendChild(probe);

    var FALLBACK = [[31, 79, 255, 0.32], [0, 200, 255, 0.20], [31, 79, 255, 1]];
    function mk(f) { return { r: f[0], g: f[1], b: f[2], a: f[3] }; }
    colors = FALLBACK.map(mk);
    var colorsRead = false;
    // 节点尚未挂入文档时 getComputedStyle 返回空串：此时保留兜底色，等 isConnected 后再读
    function readColors() {
      if (!probe.isConnected) return false;
      var names = ["--glow-a", "--glow-b", "--brand"];
      var next = names.map(function (n, i) {
        probe.style.color = "";
        probe.style.color = "var(" + n + ")";
        var c = getComputedStyle(probe).color;
        var m = c && c.match(/[\d.]+/g);
        if (!m || m.length < 3) return mk(FALLBACK[i]);
        return { r: +m[0] | 0, g: +m[1] | 0, b: +m[2] | 0, a: m.length > 3 ? parseFloat(m[3]) : 1 };
      });
      // 品牌色本身不透明，作为点缀降低强度
      next[2].a = Math.min(next[2].a, 0.28);
      colors = next; colorsRead = true;
      stats.colors = next.map(function (c) { return [c.r, c.g, c.b, +c.a.toFixed(3)]; });
      return true;
    }
    function rgba(c, k) { return "rgba(" + c.r + "," + c.g + "," + c.b + "," + (c.a * k).toFixed(3) + ")"; }

    function resize() {
      var rect = container.getBoundingClientRect();
      var s = Math.min(window.devicePixelRatio || 1, 1.5);
      var w = Math.max(1, Math.round(rect.width * s)), h = Math.max(1, Math.round(rect.height * s));
      if (w !== W || h !== H) { W = canvas.width = w; H = canvas.height = h; }
    }

    // 色斑：基准位置、半径、慢速李萨如漂移（周期 ~40-90 s）
    var blobs = [
      { c: 0, x: .22, y: .22, r: .62, ax: .10, ay: .08, px: 61, py: 83, ph: 0 },
      { c: 1, x: .80, y: .80, r: .58, ax: .09, ay: .10, px: 73, py: 57, ph: 2.1 },
      { c: 2, x: .52, y: .50, r: .44, ax: .12, ay: .09, px: 89, py: 67, ph: 4.0 },
      { c: 0, x: .88, y: .18, r: .38, ax: .07, ay: .08, px: 53, py: 97, ph: 1.3 }
    ];

    function draw(t) {
      var d = Math.max(W, H), TAU = Math.PI * 2;
      ctx.clearRect(0, 0, W, H);
      for (var i = 0; i < blobs.length; i++) {
        var b = blobs[i], col = colors[b.c];
        var x = (b.x + b.ax * Math.sin(TAU * t / b.px + b.ph)) * W;
        var y = (b.y + b.ay * Math.cos(TAU * t / b.py + b.ph)) * H;
        var r = b.r * d * (1 + 0.06 * Math.sin(TAU * t / (b.px * 0.7) + b.ph));
        var g = ctx.createRadialGradient(x, y, 0, x, y, r);
        g.addColorStop(0, rgba(col, 1));
        g.addColorStop(0.45, rgba(col, 0.42));
        g.addColorStop(1, rgba(col, 0));
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, W, H);
      }
    }

    function fail() {
      dead = true; stop();
      container.classList.remove("gl-on");
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    }
    function frame(now) {
      raf = 0;
      if (dead || !inView || document.hidden) { stats.running = 0; return; }
      raf = requestAnimationFrame(frame);
      if (now - last < FRAME_MS - 1) return;
      last = now;
      try {
        draw(now / 1000);
        stats.frames++;
        if (!shown) {
          shown = true;
          container.classList.add("gl-on");
        }
      } catch (e) { fail(); }
    }
    function start() {
      if (raf || dead || !inView || document.hidden) return;
      stats.running = 1;
      raf = requestAnimationFrame(frame);
    }
    function stop() { if (raf) cancelAnimationFrame(raf); raf = 0; stats.running = 0; }

    try {
      readColors(); resize();
      // 首帧同步绘制，成功后再触发交叉淡入
      draw(0); stats.frames++;
    } catch (e) { fail(); return { dispose: dispose }; }

    function onVis() { if (document.hidden) stop(); else start(); }
    document.addEventListener("visibilitychange", onVis);
    reg(function () { document.removeEventListener("visibilitychange", onVis); });

    var ro = null, onWin = null;
    function onSize() { try { if (!colorsRead) readColors(); resize(); if (!raf) draw(performance.now() / 1000); } catch (e) { fail(); } }
    if ("ResizeObserver" in window) { ro = new ResizeObserver(onSize); ro.observe(container); reg(function () { ro.disconnect(); }); }
    else { onWin = onSize; window.addEventListener("resize", onWin); reg(function () { window.removeEventListener("resize", onWin); }); }

    // 主题切换时重读颜色
    var mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
    var onTheme = function () { try { readColors(); } catch (e) { /* ignore */ } };
    if (mq && mq.addEventListener) { mq.addEventListener("change", onTheme); reg(function () { mq.removeEventListener("change", onTheme); }); }
    var mo = null;
    if ("MutationObserver" in window) {
      mo = new MutationObserver(onTheme);
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
      reg(function () { mo.disconnect(); });
    }

    if (typeof motion.watch === "function") {
      var stopWatch = motion.watch(container, function () { inView = true; start(); }, function () { inView = false; stop(); }, { threshold: 0 });
      reg(stopWatch);
    }
    reg(function () { dead = true; stop(); });
    reg(function () { if (probe.parentNode) probe.parentNode.removeChild(probe); });

    // 首帧已绘制：下一帧触发 opacity 交叉（需先让 opacity:0 提交一次）
    requestAnimationFrame(function () { if (!dead) { readColors(); shown = true; container.classList.add("gl-on"); } });
    start();
    return { dispose: dispose };
  }

  window.AIVCGlow = { mount: mount, stats: stats };
})();
