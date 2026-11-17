/* Dashboard graphs, drawn with Chart.js (static/js/vendor/chart.umd.js, MIT). Data comes from apps/core/analytics.py via #dash-data. */
(function () {
  "use strict";
  var node = document.getElementById("dash-data");
  if (!node) return;
  var data;
  try { data = JSON.parse(node.textContent); } catch (e) { return; }
  var noChart = typeof Chart === "undefined";

  var cs = getComputedStyle(document.body);
  function cssv(name, fallback) { return cs.getPropertyValue(name).trim() || fallback; }
  var TEXT = cssv("--text", "#1f2937"), MUTED = cssv("--muted", "#64748b"), GRID = cssv("--border", "#e5e7eb"), SURFACE = cssv("--surface", "#ffffff");

  if (!noChart) {
    Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
    Chart.defaults.font.size = 12;
    Chart.defaults.color = MUTED;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.boxWidth = 8;
    Chart.defaults.plugins.legend.labels.boxHeight = 8;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
    Chart.defaults.animation.duration = 450;
  }

  function h(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }

  function msg(host, text) {
    var p = document.createElement("p");
    p.className = "dg-empty";
    p.textContent = text;
    host.appendChild(p);
  }
  function canvasIn(host, cls) {
    var wrap = document.createElement("div");
    wrap.className = "dg-canvas " + (cls || "");
    var cv = document.createElement("canvas");
    wrap.appendChild(cv);
    host.appendChild(wrap);
    return cv;
  }
  function withAlpha(hex, alpha) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex;
    var n = parseInt(m[1], 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
  }
  var valueAxis = { beginAtZero: true, border: { display: false }, ticks: { precision: 0 }, grid: { color: GRID } };
  var catAxis = { border: { display: false }, grid: { display: false } };
  function linkClick(items) {
    return function (evt, els) {
      if (els.length && items[els[0].index] && items[els[0].index].href) window.location.href = items[els[0].index].href;
    };
  }
  function pointer(items) {
    return function (evt, els) { evt.native.target.style.cursor = els.length && items[els[0].index] && items[els[0].index].href ? "pointer" : "default"; };
  }

  function bars(host, c) {
    var items = c.items;
    var horizontal = items.length > 6 || items.some(function (i) { return i.label.length > 14; });
    new Chart(canvasIn(host, horizontal ? "dg-tall" : ""), {
      type: "bar",
      data: {
        labels: items.map(function (i) { return i.label; }),
        datasets: [{ data: items.map(function (i) { return i.value; }), backgroundColor: items.map(function (i) { return i.color; }),
          borderRadius: 8, borderSkipped: false, maxBarThickness: horizontal ? 22 : 46 }]
      },
      options: {
        indexAxis: horizontal ? "y" : "x", responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (ctx) { return " " + ctx.parsed[horizontal ? "x" : "y"]; } } } },
        scales: horizontal ? { x: valueAxis, y: catAxis } : { x: catAxis, y: valueAxis },
        onClick: linkClick(items), onHover: pointer(items)
      }
    });
  }

  function donut(host, c) {
    var items = c.items, total = items.reduce(function (a, i) { return a + i.value; }, 0);
    var centre = {
      id: "centreTotal",
      afterDraw: function (chart) {
        var meta = chart.getDatasetMeta(0);
        if (!meta.data.length) return;
        var x = meta.data[0].x, y = meta.data[0].y, ctx = chart.ctx;
        ctx.save();
        ctx.textAlign = "center";
        ctx.fillStyle = TEXT;
        ctx.font = "700 26px system-ui, sans-serif";
        ctx.fillText(String(total), x, y + 4);
        ctx.fillStyle = MUTED;
        ctx.font = "12px system-ui, sans-serif";
        ctx.fillText("total", x, y + 22);
        ctx.restore();
      }
    };
    new Chart(canvasIn(host), {
      type: "doughnut",
      data: { labels: items.map(function (i) { return i.label; }),
        datasets: [{ data: items.map(function (i) { return i.value; }), backgroundColor: items.map(function (i) { return i.color; }),
          borderColor: SURFACE, borderWidth: 3, hoverOffset: 6 }] },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "68%",
        plugins: { legend: { position: "right", labels: { padding: 14 } },
          tooltip: { callbacks: { label: function (ctx) { return " " + ctx.label + ": " + ctx.parsed + " (" + Math.round((ctx.parsed / total) * 100) + "%)"; } } } },
        onClick: linkClick(items), onHover: pointer(items)
      },
      plugins: [centre]
    });
  }

  function line(host, c) {
    var many = c.series.length > 1;
    new Chart(canvasIn(host), {
      type: "line",
      data: { labels: c.labels, datasets: c.series.map(function (se) {
        return { label: se.name, data: se.values, borderColor: se.color, borderWidth: 2.5, tension: 0.35,
          pointRadius: c.labels.length > 30 ? 0 : 3, pointHoverRadius: 5, pointBackgroundColor: se.color,
          fill: !!c.area && !many,
          backgroundColor: function (ctx) {
            var area = ctx.chart.chartArea;
            if (!area || !c.area || many) return withAlpha(se.color, 0.12);
            var g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
            g.addColorStop(0, withAlpha(se.color, 0.30));
            g.addColorStop(1, withAlpha(se.color, 0.02));
            return g;
          } };
      }) },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: many, position: "bottom" } },
        scales: { x: { border: { display: false }, grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } }, y: valueAxis }
      }
    });
  }

  function stacked(host, c) {
    var sets = c.series.map(function (se) {
      return { type: "bar", label: se.name, data: se.values, backgroundColor: se.color, borderRadius: 4, borderSkipped: false, maxBarThickness: 40, stack: "hours" };
    });
    if (c.line) {
      sets.push({ type: "line", label: c.line.name, data: c.line.values, borderColor: c.line.color, borderWidth: 2, borderDash: [6, 4],
        pointRadius: 0, pointHoverRadius: 4, tension: 0, order: 0 });
    }
    new Chart(canvasIn(host), {
      type: "bar",
      data: { labels: c.labels, datasets: sets },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: { x: { stacked: true, border: { display: false }, grid: { display: false }, ticks: { maxTicksLimit: 10, maxRotation: 0 } },
          y: { stacked: true, beginAtZero: true, border: { display: false }, ticks: { precision: 0 }, grid: { color: GRID } } }
      }
    });
  }

  // The two ranked cards ("Tickets by Status", "Tickets per project"): each has its own date range; changing it
  // fetches fresh counts and redraws just that card (no page reload).
  function rankedCard(c) {
    var host = document.getElementById("dg-" + c.id), card = document.getElementById("dg-card-" + c.id);
    var sub = document.getElementById("dg-sub-" + c.id), rangeBox = document.getElementById("dg-range-" + c.id);
    var state = { from: c.from, to: c.to };
    var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    function fmt(iso) {  // 2026-09-23 -> "23 Sep 2026", the same in every browser and language
      var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
      return m ? m[3] + " " + MONTHS[parseInt(m[2], 10) - 1] + " " + m[1] : iso;
    }
    function rangeText(from, to) { return from === to ? fmt(from) : fmt(from) + " \u2013 " + fmt(to); }
    function render(d) {
      host.textContent = "";
      var tickets = d.total + " ticket" + (d.total === 1 ? "" : "s");
      if (c.type === "project") {
        var n = d.items.length;
        sub.textContent = (n >= (d.limit || 10) ? "Top " + n + " projects" : n + " project" + (n === 1 ? "" : "s")) + " \u00b7 " + tickets;
      } else {
        sub.textContent = (d.from === d.to ? "Raised on " + fmt(d.from) : "Raised " + fmt(d.from) + " to " + fmt(d.to)) + " \u00b7 " + tickets;
      }
      if (!d.items.length) { msg(host, "No tickets were raised in this date range."); return; }
      var max = d.items[0].value;
      d.items.forEach(function (i) {
        var row = h(i.href ? "a" : "div", { class: "sc-row" });
        if (i.href) row.setAttribute("href", i.href);
        var label = h("span", { class: "sc-label" }, i.label);
        if (i.title) label.setAttribute("title", i.title);
        row.appendChild(label);
        var track = h("span", { class: "sc-track" });
        track.appendChild(h("i", { class: "sc-fill", style: "width:" + Math.max(2, (i.value / max) * 94) + "%;background:" + i.color }));
        row.appendChild(track);
        row.appendChild(h("span", { class: "sc-count" }, String(i.value)));
        host.appendChild(row);
      });
    }
    function problem(text) { host.textContent = ""; msg(host, text); }
    function load() {
      card.classList.add("is-loading");
      fetch(card.dataset.url + "?from=" + encodeURIComponent(state.from) + "&to=" + encodeURIComponent(state.to),
        { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) { if (res.ok) render(res.j); else problem(res.j.error || "Could not load the tickets."); })
        .catch(function () { problem("Could not load the tickets. Please try again."); })
        .then(function () { card.classList.remove("is-loading"); });
    }

    // ---- the one date control: a button showing the range, with a small panel (quick choices + own dates)
    function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
    function daysBack(n) { var d = new Date(); d.setDate(d.getDate() - (n - 1)); return d; }
    function dayAgo(n) { var d = new Date(); d.setDate(d.getDate() - n); return iso(d); }
    var PRESETS = [
      ["Today", function () { return [dayAgo(0), dayAgo(0)]; }],
      ["Yesterday", function () { return [dayAgo(1), dayAgo(1)]; }],
      ["Last 7 days", function () { return [iso(daysBack(7)), iso(new Date())]; }],
      ["Last 30 days", function () { return [iso(daysBack(30)), iso(new Date())]; }],
      ["Last 90 days", function () { return [iso(daysBack(90)), iso(new Date())]; }],
      ["This month", function () { var n = new Date(); return [iso(new Date(n.getFullYear(), n.getMonth(), 1)), iso(n)]; }],
      ["Last month", function () { var n = new Date(); return [iso(new Date(n.getFullYear(), n.getMonth() - 1, 1)), iso(new Date(n.getFullYear(), n.getMonth(), 0))]; }]
    ];
    var btn = h("button", { type: "button", class: "sc-range-btn", "aria-haspopup": "true", "aria-expanded": "false" });
    var ico = h("span", { class: "sc-ico", "aria-hidden": "true" });
    ico.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>';
    var label = h("span", { class: "sc-range-text" });
    btn.appendChild(ico); btn.appendChild(label); btn.appendChild(h("span", { class: "sc-chev", "aria-hidden": "true" }, "\u25be"));
    var pop = h("div", { class: "sc-pop", role: "dialog", "aria-label": "Choose dates" });
    pop.hidden = true;
    var presets = h("ul", { class: "sc-presets" });
    var customFrom = h("input", { type: "date", "aria-label": "From date" }), customTo = h("input", { type: "date", "aria-label": "To date" });
    var err = h("p", { class: "sc-err" });
    function open(show) { pop.hidden = !show; btn.setAttribute("aria-expanded", String(show)); }
    function apply(from, to) {
      state.from = from; state.to = to;
      label.textContent = rangeText(from, to);
      customFrom.value = from; customTo.value = to;
      open(false); load();
    }
    PRESETS.forEach(function (p) {
      var li = h("li"), b = h("button", { type: "button" }, p[0]);
      b.addEventListener("click", function () { var r = p[1](); apply(r[0], r[1]); });
      li.appendChild(b); presets.appendChild(li);
    });
    var custom = h("div", { class: "sc-custom" });
    var l1 = h("label", {}, "From"), l2 = h("label", {}, "To");
    l1.appendChild(customFrom); l2.appendChild(customTo);
    var go = h("button", { type: "button", class: "sc-apply" }, "Apply");
    go.addEventListener("click", function () {
      var f = customFrom.value, t = customTo.value;
      if (!f && !t) { err.textContent = "Choose a date."; return; }
      if (!f) f = t;            // only one date given: that single day
      if (!t) t = f;
      if (f > t) { err.textContent = "The start date must be before the end date."; return; }
      err.textContent = ""; apply(f, t);
    });
    custom.appendChild(l1); custom.appendChild(l2); custom.appendChild(err); custom.appendChild(go);
    pop.appendChild(presets); pop.appendChild(custom);
    rangeBox.appendChild(btn); rangeBox.appendChild(pop);
    btn.addEventListener("click", function () { open(pop.hidden); });
    if (document.addEventListener) {
      document.addEventListener("click", function (e) { if (!pop.hidden && e.target && !(rangeBox.contains && rangeBox.contains(e.target))) open(false); });
      document.addEventListener("keydown", function (e) { if (e.key === "Escape") open(false); });
    }
    label.textContent = rangeText(state.from, state.to);
    customFrom.value = state.from; customTo.value = state.to;
    render(c);
  }

  var draw = { bars: bars, donut: donut, line: line, stacked: stacked };
  (data.sections || []).forEach(function (sec) {
    sec.charts.forEach(function (c) {
      if (c.type === "status" || c.type === "project") { rankedCard(c); return; }
      var host = document.getElementById("dg-" + c.id);
      if (!host) return;
      if (c.empty) { msg(host, "No data for this period."); return; }
      if (noChart) { msg(host, "The graph library did not load. Please refresh the page."); return; }
      try { draw[c.type](host, c); } catch (e) { msg(host, "This graph could not be drawn."); }
    });
  });
})();
