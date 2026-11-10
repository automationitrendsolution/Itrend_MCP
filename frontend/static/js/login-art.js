// Sign-in page artwork: slow, flowing ribbons of light in the iTrend Solution colours.
//
// Drawn on a <canvas data-login-art> at low resolution and scaled up by the browser,
// which makes the ribbons naturally soft and keeps the animation cheap.
// - Respects "reduce motion": one still frame, no animation.
// - Pauses when the panel is hidden (phones) or the tab is in the background.

(function () {
  "use strict";

  const canvas = document.querySelector("[data-login-art]");
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext("2d");
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SCALE = 0.4; // drawing resolution relative to the screen: soft edges, little work

  // Brand colours from the logo's swirl.
  const RIBBONS = [
    { colors: ["#e0103a", "#ff7a00"], y: -0.34, amp: 0.07, width: 0.34, speed: 0.10, freq: 0.9, phase: 0.0 },
    { colors: ["#ff7a00", "#ffb000"], y: -0.16, amp: 0.08, width: 0.26, speed: 0.12, freq: 1.1, phase: 1.1 },
    { colors: ["#13a3b5", "#2f6bff"], y: 0.00, amp: 0.09, width: 0.30, speed: 0.08, freq: 1.0, phase: 2.2 },
    { colors: ["#c2187a", "#6b2fa0"], y: 0.16, amp: 0.07, width: 0.32, speed: 0.11, freq: 1.3, phase: 3.3 },
    { colors: ["#ff7a00", "#e0103a"], y: 0.32, amp: 0.06, width: 0.24, speed: 0.13, freq: 1.5, phase: 4.4 },
    { colors: ["#6b2fa0", "#13a3b5"], y: 0.46, amp: 0.05, width: 0.20, speed: 0.09, freq: 1.2, phase: 5.5 },
  ];

  let width = 0, height = 0, frame = 0;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    width = Math.max(1, Math.round(rect.width * SCALE));
    height = Math.max(1, Math.round(rect.height * SCALE));
    canvas.width = width;
    canvas.height = height;
  }

  // One ribbon: a band between two waving curves, across the panel at an angle.
  function ribbon(r, t) {
    const span = Math.hypot(width, height);
    const steps = 48;
    const top = [], bottom = [];
    for (let i = 0; i <= steps; i++) {
      const u = i / steps;
      const x = (u - 0.5) * span * 1.3;
      const wave = Math.sin(u * Math.PI * 2 * r.freq + t * r.speed * 6 + r.phase);
      const swell = 0.6 + 0.4 * Math.sin(u * Math.PI + t * r.speed * 3 + r.phase);
      const y = (r.y + r.amp * wave) * span * 0.8;
      const half = (r.width * swell * span) / 4;
      top.push([x, y - half]);
      bottom.push([x, y + half]);
    }
    const gradient = ctx.createLinearGradient(-span * 0.6, 0, span * 0.6, 0);
    gradient.addColorStop(0, "rgba(0,0,0,0)");
    gradient.addColorStop(0.2, r.colors[0]);
    gradient.addColorStop(0.75, r.colors[1]);
    gradient.addColorStop(1, "rgba(0,0,0,0)");
    ctx.beginPath();
    ctx.moveTo(top[0][0], top[0][1]);
    top.forEach(([x, y]) => ctx.lineTo(x, y));
    bottom.reverse().forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // A thin bright edge, like light catching the fold of the ribbon.
    ctx.beginPath();
    ctx.moveTo(top[0][0], top[0][1]);
    top.forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.strokeStyle = "rgba(255, 214, 170, 0.35)";
    ctx.lineWidth = Math.max(1, span * 0.004);
    ctx.stroke();
  }

  function draw(time) {
    const t = time / 1000;
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = "#08090c";
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.translate(width / 2, height / 2);
    ctx.rotate(-0.95); // ribbons rise from bottom-left to top-right, across the whole panel
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.72;
    if ("filter" in ctx) ctx.filter = "blur(" + Math.max(1, Math.round(width * 0.012)) + "px)"; // silky, not sharp
    RIBBONS.forEach((r) => ribbon(r, t));
    ctx.restore();
    if ("filter" in ctx) ctx.filter = "none";

    // Soft vignette so the welcome text stays readable.
    const vignette = ctx.createRadialGradient(width / 2, height / 2, Math.min(width, height) * 0.15, width / 2, height / 2, Math.max(width, height) * 0.75);
    vignette.addColorStop(0, "rgba(8, 9, 12, 0.45)");
    vignette.addColorStop(1, "rgba(8, 9, 12, 0.4)");
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, width, height);
  }

  function loop(time) {
    if (canvas.offsetParent !== null) draw(time); // skip work while the panel is hidden
    frame = window.requestAnimationFrame(loop);
  }

  resize();
  window.addEventListener("resize", () => { resize(); if (reduceMotion) draw(4000); });
  if (reduceMotion) {
    draw(4000); // one calm still frame
  } else {
    frame = window.requestAnimationFrame(loop);
  }
})();
