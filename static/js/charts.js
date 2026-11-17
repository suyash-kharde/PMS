/* tiny canvas charts — no chart library */
(function () {
  "use strict";

  function css(name, fallback) {
    return getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
  }

  function scale(canvas) {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || canvas.parentElement.clientWidth;
    const height = parseInt(canvas.getAttribute("height"), 10) || 200;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    return { ctx: ctx, w: width, h: height };
  }

  function bars(canvas, data) {
    const { ctx, w, h } = scale(canvas);
    const pad = 28;
    const max = Math.max(1, ...data.map((d) => d.value));
    const bw = (w - pad * 2) / data.length;
    ctx.clearRect(0, 0, w, h);
    ctx.font = "11px system-ui";
    data.forEach((d, i) => {
      const bh = ((h - pad * 2) * d.value) / max;
      const x = pad + i * bw + bw * 0.15;
      const y = h - pad - bh;
      ctx.fillStyle = d.color;
      ctx.beginPath();
      ctx.roundRect(x, y, bw * 0.7, bh, 6);
      ctx.fill();
      ctx.fillStyle = css("--text", "#101828");
      ctx.textAlign = "center";
      ctx.fillText(d.value, x + bw * 0.35, y - 5);
      ctx.fillStyle = css("--muted", "#667085");
      ctx.fillText(d.label.slice(0, 10), x + bw * 0.35, h - 10);
    });
  }

  function lines(canvas, data) {
    const { ctx, w, h } = scale(canvas);
    const pad = 30;
    const max = Math.max(1, ...data.map((d) => Math.max(d.created, d.done)));
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = css("--border", "#e2e6ef");
    ctx.beginPath();
    ctx.moveTo(pad, h - pad); ctx.lineTo(w - pad / 2, h - pad);
    ctx.stroke();

    function series(key, color) {
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      data.forEach((d, i) => {
        const x = pad + (i * (w - pad * 1.5)) / (data.length - 1 || 1);
        const y = h - pad - ((h - pad * 2) * d[key]) / max;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    }
    series("created", css("--primary", "#4f46e5"));
    series("done", "#16a34a");

    ctx.font = "10px system-ui";
    ctx.fillStyle = css("--muted", "#667085");
    ctx.textAlign = "center";
    data.forEach((d, i) => {
      if (i % 3) return;
      const x = pad + (i * (w - pad * 1.5)) / (data.length - 1 || 1);
      ctx.fillText(d.label, x, h - 10);
    });
    ctx.textAlign = "left";
    ctx.fillStyle = css("--primary", "#4f46e5"); ctx.fillText("created", pad, 14);
    ctx.fillStyle = "#16a34a"; ctx.fillText("completed", pad + 56, 14);
  }

  function draw() {
    const s = document.getElementById("statesChart");
    if (s) bars(s, JSON.parse(s.dataset.chart || "[]"));
    const b = document.getElementById("burnChart");
    if (b) lines(b, JSON.parse(b.dataset.chart || "[]"));
  }

  draw();
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(draw, 150); });
  document.addEventListener("click", (e) => { if (e.target.closest("[data-toggle-theme]")) setTimeout(draw, 30); });
})();
