/* home-tunnel：首屏「LED 像素隧道」背景。原生 WebGL2，无库。
 * 约 120 条细长色条分左右两墙沿 z 轴飞向镜头并循环复用，再过一层 LED 像素化后处理。
 * 接口：window.AIVCTunnel = { mount(container, api) -> bool, setBoost(bool), stats }
 *   mount 返回 false 表示 WebGL2 不可用/着色器失败（已清理），调用方应回退到 AIVCGlow。
 *   <768 宽只显示 CSS 渐变静态回退（返回 true，不创建 WebGL）；reduced-motion 只渲染一帧。
 * 画布 mix-blend-mode: lighten，面向深色背景；中间区域由后处理压暗，给标题让位。 */
(function () {
  "use strict";

  // ---- 规格参数（R-HERO-01~04） ----
  var BAR_COUNT = 130;          // 常驻色条
  var BOOST_BARS = 50;          // 加速光条
  var FOV_DEG = 75;
  var SPEED_START = 30, SPEED_END = 12, SPEED_EASE_S = 4;   // 单位/s，三次缓出
  var K_MIN = 1.2, K_MAX = 2.0;                              // 每条速度随机系数
  var DT_MAX = 0.1;
  var BOOST_SPEED = 40, LP_SPEED = 0.08;                     // 速度额外 +40，低通 0.08/帧
  var BOOST_SQUEEZE = 0.15, LP_SQUEEZE = 0.08;               // 两墙收拢 15%
  var LP_LIGHT = 0.04, LIGHT_VISIBLE = 0.01;                 // 光条低通 0.04/帧，阈值 0.01
  var PARALLAX = [0.015, -0.02], LP_PARALLAX = 0.03;
  var SCAN_PERIOD = 8.3, SCAN_GAIN = 0.7;
  var MIN_WIDTH = 768;
  var FAR = 42, NEAR_CUT = 0.4;

  var stats = { frames: 0, running: 0, mode: "none", boost: 0, light: 0, squeeze: 0, speed: 0, cell: 0, renderW: 0, renderH: 0 };
  var current = null;

  // ---- 着色器 ----
  var VS_SCENE = [
    "#version 300 es",
    "precision highp float;",
    "layout(location=0) in vec4 aA;",   // x(带墙符号), yc, 半高, d
    "layout(location=1) in vec4 aB;",   // 半长(z), r, g, b
    "layout(location=2) in vec4 aC;",   // alpha, 近端青绿高光强度
    "uniform mat3 uRot; uniform float uAspect; uniform float uSqueeze;",
    "out vec3 vCol;",
    "out vec3 vHi;",
    "void main(){",
    "  int vid = gl_VertexID;",
    "  float sx = float(vid & 1), sy = float(vid >> 1);",
    "  float z = max(mix(aA.w - aB.x, aA.w + aB.x, sx), 0.2);",
    "  float y = aA.y + (sy * 2.0 - 1.0) * aA.z;",
    "  vec3 p = uRot * vec3(aA.x * uSqueeze, y, z);",
    "  float t = tan(radians(" + FOV_DEG.toFixed(1) + ") * 0.5);",
    "  float w = max(p.z, 0.05);",
    "  gl_Position = vec4(p.x / (t * uAspect), p.y / t, 0.0, w);",
    "  float near = 1.0 - sx;",
    "  vCol = aB.yzw * aC.x;",
    "  vHi = vec3(near * aC.y, aC.x, 0.0);",
    "}"
  ].join("\n");
  var FS_SCENE = [
    "#version 300 es",
    "precision mediump float;",
    "in vec3 vCol; in vec3 vHi; out vec4 o;",
    "void main(){",
    "  float h = smoothstep(0.55, 1.0, vHi.x);",
    "  vec3 hi = vec3(0.02, 0.85, 0.80) * vHi.y;",
    "  o = vec4(mix(vCol, hi, h * 0.85), 1.0);",
    "}"
  ].join("\n");

  var VS_POST = [
    "#version 300 es",
    "void main(){",
    "  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));",
    "  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);",
    "}"
  ].join("\n");
  var FS_POST = [
    "#version 300 es",
    "precision highp float;",
    "uniform sampler2D uScene;",
    "uniform vec2 uRes;",        // 画布设备像素
    "uniform float uCell;",      // 格子设备像素
    "uniform vec2 uCells;",      // 格子数
    "uniform float uTime;",
    "uniform float uScanGain; uniform float uScanPeriod;",
    "out vec4 o;",
    "float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }",
    "float sdBox(vec2 p, vec2 b){ vec2 d = abs(p) - b; return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0); }",
    "void main(){",
    "  vec2 px = gl_FragCoord.xy;",
    "  vec2 cid = floor(px / uCell);",
    "  vec2 f = fract(px / uCell);",
    "  vec2 uv = (cid + 0.5) / uCells;",
    "  vec2 st = vec2(1.0 / uCells.x, 1.0 / uCells.y);",
    // 轻微色差：R/B 通道沿 x 反向偏 0.35 格
    "  float ca = 0.06 * st.x;",
    "  vec3 c = vec3(texture(uScene, uv + vec2(ca, 0.0)).r, texture(uScene, uv).g, texture(uScene, uv - vec2(ca, 0.0)).b);",
    // 极弱泛光：四邻格
    "  vec3 bl = texture(uScene, uv + vec2(st.x, 0.0)).rgb + texture(uScene, uv - vec2(st.x, 0.0)).rgb",
    "          + texture(uScene, uv + vec2(0.0, st.y)).rgb + texture(uScene, uv - vec2(0.0, st.y)).rgb;",
    "  c += bl * 0.03;",
    // 中间让位给标题
    "  float nx = abs(uv.x * 2.0 - 1.0);",
    "  c *= smoothstep(0.34, 0.78, nx);",
    "  c *= smoothstep(0.97, 0.84, uv.y);",
    // 每格亮度抖动 ±4%
    "  float j = hash(cid + floor(uTime * 10.0) * 7.13);",
    "  c *= 1.0 + (j - 0.5) * 0.08;",
    // 扫描亮线：自上而下，每 uScanPeriod 秒一次
    "  float sp = 1.0 - fract(uTime / uScanPeriod);",
    "  float dy = (uv.y - sp) / 0.04;",
    "  c *= 1.0 + uScanGain * exp(-dy * dy);",
    // RGB 子像素：格内横向三等分，每条为圆角竖条，条间/格间留暗缝
    "  float k = floor(f.x * 3.0);",
    "  vec3 sel = vec3(k < 0.5 ? 1.0 : 0.0, (k > 0.5 && k < 1.5) ? 1.0 : 0.0, k > 1.5 ? 1.0 : 0.0);",
    "  float lit = dot(c * vec3(0.8, 1.0, 1.0), sel);",
    "  vec2 lp = vec2((fract(f.x * 3.0) - 0.5) / 3.0, f.y - 0.5);",
    "  float rad = 0.07;",
    "  float d = sdBox(lp, vec2(1.0 / 6.0 - 0.035, 0.5 - 0.09) - rad) - rad;",
    "  float aa = 1.0 / uCell;",
    "  float m = smoothstep(aa, -aa, d);",
    "  vec3 col = sel * lit * m * 2.1 + c * 0.015;",
    "  o = vec4(col, 1.0);",
    "}"
  ].join("\n");

  // ---- 工具 ----
  function mixHex(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
  function parseColor(str) {
    var m = String(str || "").match(/[\d.]+/g);
    if (!m || m.length < 3) return null;
    return [m[0] / 255, m[1] / 255, m[2] / 255];
  }
  function compile(gl, type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { var e = gl.getShaderInfoLog(s); gl.deleteShader(s); throw new Error("shader: " + e); }
    return s;
  }
  function program(gl, vs, fs) {
    var p = gl.createProgram(), a = compile(gl, gl.VERTEX_SHADER, vs), b = compile(gl, gl.FRAGMENT_SHADER, fs);
    gl.attachShader(p, a); gl.attachShader(p, b); gl.linkProgram(p);
    gl.deleteShader(a); gl.deleteShader(b);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) { var e = gl.getProgramInfoLog(p); gl.deleteProgram(p); throw new Error("link: " + e); }
    return p;
  }

  function mount(container, api) {
    if (!container) return false;
    if (current) { try { current.dispose(); } catch (e) { /* ignore */ } current = null; }
    var motion = (api && api.motion) || {};
    var cleanups = [], disposed = false;
    function reg(fn) {
      if (typeof motion.onCleanup === "function") motion.onCleanup(fn);
      cleanups.push(fn);
    }
    function dispose() {
      if (disposed) return; disposed = true;
      var l = cleanups; cleanups = [];
      l.forEach(function (f) { try { f(); } catch (e) { /* ignore */ } });
      stats.running = 0; stats.mode = "none"; stats.boost = 0;
      if (current === inst) current = null;
    }

    var mqReduce = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    var reduced = typeof motion.reduced === "boolean" ? motion.reduced : !!(mqReduce && mqReduce.matches);
    var mqWide = window.matchMedia ? window.matchMedia("(min-width: " + MIN_WIDTH + "px)") : null;
    function isWide() { return mqWide ? mqWide.matches : window.innerWidth >= MIN_WIDTH; }

    var fallback = document.createElement("div");
    fallback.className = "tn-fallback";
    fallback.setAttribute("aria-hidden", "true");
    container.appendChild(fallback);
    reg(function () { if (fallback.parentNode) fallback.parentNode.removeChild(fallback); container.classList.remove("tn-on"); });

    var inst = { dispose: dispose };
    var gfx = null;           // 当前 WebGL 会话（仅 ≥768）
    var inView = true;
    var boostFlag = 0;
    var ptr = { x: 0, y: 0 };
    var tmpProbe = null;

    // ---- 调色板（可读 --brand 微调蓝色） ----
    var palette = [
      { c: [0.10, 0.22, 1.00], w: 0.62 },   // 蓝
      { c: [0.22, 0.18, 1.00], w: 0.17 },   // 靛紫（偏蓝，不带红）
      { c: [0.05, 0.60, 1.00], w: 0.16 },   // 青
      { c: [0.10, 0.85, 0.50], w: 0.05 }    // 绿（少量点缀）
    ];
    function readBrand() {
      try {
        if (!tmpProbe) {
          tmpProbe = document.createElement("span");
          tmpProbe.style.cssText = "position:absolute;width:0;height:0;visibility:hidden";
          container.appendChild(tmpProbe);
          reg(function () { if (tmpProbe && tmpProbe.parentNode) tmpProbe.parentNode.removeChild(tmpProbe); });
        }
        tmpProbe.style.color = "";
        tmpProbe.style.color = "var(--brand)";
        var rgb = parseColor(getComputedStyle(tmpProbe).color);
        if (rgb) {
          var mx = Math.max(rgb[0], rgb[1], rgb[2], 0.001);
          var norm = [rgb[0] / mx, rgb[1] / mx, rgb[2] / mx];
          palette[0].c = mixHex([0.10, 0.22, 1.00], norm, 0.25);   // 仅 25% 偏向品牌色
        }
      } catch (e) { /* 保持默认 */ }
    }
    function pickColor() {
      var r = Math.random(), acc = 0;
      for (var i = 0; i < palette.length; i++) { acc += palette[i].w; if (r <= acc) return palette[i].c; }
      return palette[0].c;
    }

    // ---- 色条状态 ----
    var N = BAR_COUNT + BOOST_BARS;
    var bars = [];
    function respawn(b, initial) {
      var boost = b.boost;
      b.side = Math.random() < 0.5 ? -1 : 1;
      // 色条沿 z 拉长，贴墙（x=±墙距），高度很小 → 透视下成为向中心汇聚的斜向条带
      b.x = b.side * (6.5 + Math.random() * 3.5);
      // 纵向：两簇，集中在地平线附近（上方一簇为主，下方一簇较少），条带近水平
      b.yc = Math.random() < 0.68 ? 0.8 + Math.random() * 4.2 : -2.6 + Math.random() * 2.6;
      b.hh = boost ? 0.8 + Math.random() * 1.2 : 0.3 + Math.random() * 0.75;
      b.hl = boost ? 1.5 + Math.random() * 3 : 2 + Math.random() * 4.5;
      b.k = K_MIN + Math.random() * (K_MAX - K_MIN);
      if (boost) b.k *= 1.4;
      b.col = boost ? mixHex(pickColor(), [0.2, 0.8, 1], 0.4) : pickColor();
      b.gain = boost ? 1.8 : 0.55 + Math.random() * 0.5;
      b.hi = boost ? 0 : (Math.random() < 0.35 ? 0.6 + Math.random() * 0.4 : 0);
      b.d = initial ? 2 + Math.random() * (FAR - 2) : FAR + Math.random() * 4;
    }
    for (var bi = 0; bi < N; bi++) { bars.push({ boost: bi >= BAR_COUNT }); respawn(bars[bi], true); }

    // ---- 动画状态 ----
    var tAnim = 0;           // 速度缓出用的累计时间
    var boostV = 0, boostSq = 0, boostLt = 0;
    var rotX = 0, rotY = 0;
    var raf = 0, last = 0, wallT = 0;

    function lowpass(cur, target, a, dt) { return cur + (target - cur) * (1 - Math.pow(1 - a, dt * 60)); }
    function baseSpeed(t) {
      if (t >= SPEED_EASE_S) return SPEED_END;
      var p = t / SPEED_EASE_S, e = 1 - Math.pow(1 - p, 3);
      return SPEED_START + (SPEED_END - SPEED_START) * e;
    }

    // ---- WebGL 会话 ----
    function glStart() {
      var canvas = document.createElement("canvas");
      canvas.className = "tn-canvas";
      canvas.setAttribute("aria-hidden", "true");
      var gl = canvas.getContext("webgl2", { alpha: false, antialias: false, depth: false, stencil: false, powerPreference: "high-performance" });
      if (!gl) throw new Error("no webgl2");
      var progS = program(gl, VS_SCENE, FS_SCENE), progP = program(gl, VS_POST, FS_POST);
      var uS = {}, uP = {};
      ["uRot", "uAspect", "uSqueeze"].forEach(function (n) { uS[n] = gl.getUniformLocation(progS, n); });
      ["uScene", "uRes", "uCell", "uCells", "uTime", "uScanGain", "uScanPeriod"].forEach(function (n) { uP[n] = gl.getUniformLocation(progP, n); });

      var vaoS = gl.createVertexArray(), vaoP = gl.createVertexArray();
      var buf = gl.createBuffer();
      var data = new Float32Array(N * 12);
      gl.bindVertexArray(vaoS);
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, gl.DYNAMIC_DRAW);
      for (var a = 0; a < 3; a++) {
        gl.enableVertexAttribArray(a);
        gl.vertexAttribPointer(a, 4, gl.FLOAT, false, 48, a * 16);
        gl.vertexAttribDivisor(a, 1);
      }
      gl.bindVertexArray(null);

      var tex = gl.createTexture(), fbo = gl.createFramebuffer();
      var S = { canvas: canvas, gl: gl, cssW: 0, cssH: 0, cells: [1, 1], cellDev: 6, lost: false };

      function resize() {
        var rect = container.getBoundingClientRect();
        var dpr = Math.min(window.devicePixelRatio || 1, 2);
        var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
        var cellCss = (rect.width < 1024 || coarse) ? 4 : 6;
        var w = Math.max(1, Math.round(rect.width * dpr)), h = Math.max(1, Math.round(rect.height * dpr));
        var cellDev = cellCss * dpr;
        var cx = Math.max(1, Math.ceil(w / cellDev)), cy = Math.max(1, Math.ceil(h / cellDev));
        if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
        S.cellDev = cellDev; S.cells = [cx, cy];
        // 场景纹理：每格 2×2 个纹素，后处理在格心取样时由双线性自动做 2×2 盒滤波
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, cx * 2, cy * 2, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        stats.cell = cellCss; stats.renderW = w; stats.renderH = h;
      }

      function rotMat(ax, ay) {
        // 先绕 X 后绕 Y（列主序 mat3）
        var cx = Math.cos(ax), sx = Math.sin(ax), cy = Math.cos(ay), sy = Math.sin(ay);
        // R = Ry * Rx
        return new Float32Array([
          cy, sx * sy, -cx * sy,
          0, cx, sx,
          sy, -sx * cy, cx * cy
        ]);
      }

      S.draw = function (timeSec) {
        // 填充实例数据
        var i, b, o = 0, lightOn = boostLt > LIGHT_VISIBLE;
        for (i = 0; i < N; i++) {
          b = bars[i];
          var alpha;
          if (b.boost) alpha = lightOn ? boostLt : 0;
          else alpha = b.gain;
          var fade = Math.min(1, Math.max(0, (FAR - b.d) / (FAR * 0.45)));
          alpha *= fade;
          data[o++] = b.x; data[o++] = b.yc; data[o++] = b.hh; data[o++] = b.d;
          data[o++] = b.hl; data[o++] = b.col[0]; data[o++] = b.col[1]; data[o++] = b.col[2];
          data[o++] = alpha; data[o++] = b.hi || 0; data[o++] = 0; data[o++] = 0;
        }
        var cnt = lightOn ? N : BAR_COUNT;
        var cw = S.cells[0] * 2, ch = S.cells[1] * 2;
        // 1) 场景 → 小纹理（加法混合）
        gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
        gl.viewport(0, 0, cw, ch);
        gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
        gl.useProgram(progS);
        gl.uniformMatrix3fv(uS.uRot, false, rotMat(rotX, rotY));
        gl.uniform1f(uS.uAspect, canvas.width / canvas.height);
        gl.uniform1f(uS.uSqueeze, 1 - BOOST_SQUEEZE * boostSq);
        gl.bindVertexArray(vaoS);
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferSubData(gl.ARRAY_BUFFER, 0, data, 0, cnt * 12);
        gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, cnt);
        // 2) LED 后处理 → 画布
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.disable(gl.BLEND);
        gl.useProgram(progP);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.uniform1i(uP.uScene, 0);
        gl.uniform2f(uP.uRes, canvas.width, canvas.height);
        gl.uniform1f(uP.uCell, S.cellDev);
        gl.uniform2f(uP.uCells, S.cells[0], S.cells[1]);
        gl.uniform1f(uP.uTime, timeSec);
        gl.uniform1f(uP.uScanGain, SCAN_GAIN);
        gl.uniform1f(uP.uScanPeriod, SCAN_PERIOD);
        gl.bindVertexArray(vaoP);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      };

      S.dispose = function () {
        try {
          gl.deleteTexture(tex); gl.deleteFramebuffer(fbo); gl.deleteBuffer(buf);
          gl.deleteVertexArray(vaoS); gl.deleteVertexArray(vaoP);
          gl.deleteProgram(progS); gl.deleteProgram(progP);
          var ext = gl.getExtension("WEBGL_lose_context"); if (ext) ext.loseContext();
        } catch (e) { /* ignore */ }
        if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      };

      canvas.addEventListener("webglcontextlost", function (ev) {
        ev.preventDefault(); S.lost = true; stopLoop(); container.classList.remove("tn-on");
      });
      canvas.addEventListener("webglcontextrestored", function () { /* 保持回退层；不重建 */ });

      container.appendChild(canvas);
      resize();
      S.resize = resize;
      return S;
    }

    function glStop() {
      stopLoop();
      if (gfx) { gfx.dispose(); gfx = null; }
      container.classList.remove("tn-on");
      stats.mode = isWide() ? stats.mode : "fallback";
    }

    // ---- 主循环 ----
    function step(dt) {
      tAnim += dt;
      boostV = lowpass(boostV, boostFlag, LP_SPEED, dt);
      boostSq = lowpass(boostSq, boostFlag, LP_SQUEEZE, dt);
      boostLt = lowpass(boostLt, boostFlag, LP_LIGHT, dt);
      var base = baseSpeed(tAnim) + BOOST_SPEED * boostV;
      for (var i = 0; i < N; i++) {
        var b = bars[i];
        b.d -= base * b.k * dt;
        if (b.d + b.hl < NEAR_CUT) respawn(b, false);
      }
      // 对照参考站相机：rotation.x ← 0.015×指针y(上为正)，rotation.y ← −0.02×指针x；本模块旋转的是场景点，故 X 轴取反
      rotX = lowpass(rotX, -ptr.y * PARALLAX[0], LP_PARALLAX, dt);
      rotY = lowpass(rotY, ptr.x * PARALLAX[1], LP_PARALLAX, dt);
      stats.boost = boostV; stats.light = boostLt; stats.squeeze = boostSq; stats.speed = base;
    }
    function frame(now) {
      raf = 0;
      if (disposed || !gfx || !inView || document.hidden) { stats.running = 0; return; }
      raf = requestAnimationFrame(frame);
      var dt = last ? Math.min((now - last) / 1000, DT_MAX) : 0;
      last = now;
      wallT += dt;
      try {
        step(dt);
        gfx.draw(wallT);
        stats.frames++;
      } catch (e) { glStop(); }
    }
    function startLoop() {
      if (raf || disposed || reduced || !gfx || !inView || document.hidden) return;
      stats.running = 1; last = 0;
      raf = requestAnimationFrame(frame);
    }
    function stopLoop() { if (raf) cancelAnimationFrame(raf); raf = 0; stats.running = 0; }

    function showStatic() {
      // reduced：只渲染一帧静态画面（色条取初始分布，扫描线停在固定位置）
      try { gfx.draw(1.7); stats.frames++; } catch (e) { glStop(); }
    }

    function enterWide() {
      if (gfx) return;
      gfx = glStart();
      stats.mode = reduced ? "static" : "webgl";
      if (reduced) showStatic(); else { gfx.draw(0); stats.frames++; }
      container.classList.add("tn-on");
      startLoop();
    }

    // ---- 首次挂载：失败则清理并返回 false ----
    readBrand();
    if (isWide()) {
      try { enterWide(); }
      catch (e) { if (gfx) { gfx.dispose(); gfx = null; } dispose(); return false; }
    } else {
      stats.mode = "fallback";
    }

    // 事件
    function onVis() { if (document.hidden) stopLoop(); else startLoop(); }
    document.addEventListener("visibilitychange", onVis);
    reg(function () { document.removeEventListener("visibilitychange", onVis); });

    function onPointer(e) {
      if (reduced) return;
      ptr.x = (e.clientX / (window.innerWidth || 1)) * 2 - 1;
      ptr.y = -((e.clientY / (window.innerHeight || 1)) * 2 - 1);
    }
    window.addEventListener("pointermove", onPointer, { passive: true });
    reg(function () { window.removeEventListener("pointermove", onPointer); });

    function onSize() {
      try {
        if (isWide()) {
          if (!gfx) { enterWide(); return; }
          gfx.resize();
          if (reduced) showStatic(); else if (!raf) { gfx.draw(wallT); }
        } else if (gfx) { glStop(); stats.mode = "fallback"; }
      } catch (e) { if (gfx) { gfx.dispose(); gfx = null; } container.classList.remove("tn-on"); stats.mode = "fallback"; }
    }
    if ("ResizeObserver" in window) {
      var ro = new ResizeObserver(onSize); ro.observe(container); reg(function () { ro.disconnect(); });
    } else {
      window.addEventListener("resize", onSize); reg(function () { window.removeEventListener("resize", onSize); });
    }

    // 主题切换重读品牌色
    if ("MutationObserver" in window) {
      var mo = new MutationObserver(function () { readBrand(); });
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
      reg(function () { mo.disconnect(); });
    }

    // 离开视口暂停
    if (typeof motion.watch === "function") {
      var stopWatch = motion.watch(container, function () { inView = true; startLoop(); }, function () { inView = false; stopLoop(); }, { threshold: 0 });
      reg(stopWatch);
    } else if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (es) {
        inView = es[es.length - 1].isIntersecting;
        if (inView) startLoop(); else stopLoop();
      });
      io.observe(container); reg(function () { io.disconnect(); });
    }

    reg(function () { stopLoop(); if (gfx) { gfx.dispose(); gfx = null; } });

    inst.setBoost = function (on) { if (!reduced) boostFlag = on ? 1 : 0; };
    current = inst;
    return true;
  }

  function setBoost(on) { if (current && current.setBoost) current.setBoost(!!on); }

  window.AIVCTunnel = { mount: mount, setBoost: setBoost, stats: stats };
})();
