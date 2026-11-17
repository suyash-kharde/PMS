/* dependency-free timeline: absolute-positioned bars over a day scale */
(function () {
  "use strict";
  const host = document.getElementById("gantt");
  if (!host) return;

  const rows = JSON.parse(host.dataset.rows || "[]");
  if (!rows.length) { host.innerHTML = '<p class="empty">No dated tasks to plot.</p>'; return; }

  const day = 86400000;
  const dates = rows.flatMap((r) => [new Date(r.start), new Date(r.end)]);
  let min = new Date(Math.min.apply(null, dates));
  let max = new Date(Math.max.apply(null, dates));
  min = new Date(min.getTime() - 2 * day);
  max = new Date(max.getTime() + 2 * day);
  const span = Math.max(1, Math.round((max - min) / day));

  const ticks = [];
  const step = Math.max(1, Math.round(span / 10));
  for (let i = 0; i <= span; i += step) {
    const d = new Date(min.getTime() + i * day);
    ticks.push(d.toLocaleDateString(undefined, { day: "2-digit", month: "short" }));
  }

  const grid = document.createElement("div");
  grid.className = "gantt-grid";
  grid.innerHTML =
    '<div class="gantt-head"><span style="flex:0 0 200px;border:0"></span>' +
    ticks.map((t) => "<span>" + t + "</span>").join("") + "</div>";

  rows.forEach((r) => {
    const start = (new Date(r.start) - min) / day;
    const end = (new Date(r.end) - min) / day;
    const left = (start / span) * 100;
    const width = Math.max(1.2, ((end - start + 1) / span) * 100);
    const row = document.createElement("div");
    row.className = "gantt-row";
    row.innerHTML =
      '<div class="gantt-label" title=""></div>' +
      '<div class="gantt-track"><a class="gantt-bar" style="left:' + left + "%;width:" + width + "%;--c:" + r.color + '"><span style="left:' + r.progress + '%"></span></a></div>';
    const label = row.querySelector(".gantt-label");
    label.textContent = r.title;
    label.title = r.title + (r.assignee ? " · " + r.assignee : "");
    const bar = row.querySelector(".gantt-bar");
    bar.href = r.url;
    bar.title = r.title + " · " + r.start + " → " + r.end + " · " + r.progress + "%";
    grid.appendChild(row);
  });

  const todayOffset = ((new Date() - min) / day / span) * 100;
  if (todayOffset >= 0 && todayOffset <= 100) {
    const marker = document.createElement("div");
    marker.className = "gantt-today";
    marker.style.left = "calc(200px + 10px + (100% - 210px) * " + todayOffset / 100 + ")";
    marker.title = "Today";
    grid.appendChild(marker);
  }

  host.appendChild(grid);
})();
