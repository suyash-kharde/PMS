/* sprint plan generation: live polling of the run status while the worker builds it */
(function () {
  "use strict";
  const root = document.getElementById("planRun");
  if (!root) return;

  const url = root.dataset.statusUrl;
  let terminal = root.dataset.terminal === "1";

  const badge = document.querySelector("[data-run-status]");
  const phase = document.querySelector("[data-run-phase]");
  const stepsEl = document.querySelector("[data-steps]");
  const logEl = document.querySelector("[data-log]");
  const summaryEl = document.querySelector("[data-summary]");
  const errorEl = document.querySelector("[data-error]");
  const doneActions = document.querySelector("[data-done-actions]");

  const ICONS = { done: "✓", failed: "✗", running: "◔", skipped: "–", pending: "○" };

  function renderStep(s) {
    let li = stepsEl.querySelector('[data-step="' + CSS.escape(s.key) + '"]');
    if (!li) {
      const empty = stepsEl.querySelector("[data-steps-empty]");
      if (empty) empty.remove();
      li = document.createElement("li");
      li.className = "list-row";
      li.dataset.step = s.key;
      li.innerHTML =
        '<span class="step-ico" data-step-ico></span>' +
        '<span class="grow"><strong data-step-label></strong>' +
        '<br><span class="muted small" data-step-detail></span></span>';
      stepsEl.appendChild(li);
    }
    const ico = li.querySelector("[data-step-ico]");
    ico.textContent = ICONS[s.status] || "○";
    ico.classList.toggle("step-run", s.status === "running");
    li.querySelector("[data-step-label]").textContent = s.label;
    li.querySelector("[data-step-detail]").textContent = s.detail || "";
  }

  function tick() {
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        if (badge) {
          badge.textContent = d.status_label;
          badge.className = "badge status-" + d.status;
        }
        if (phase) phase.textContent = d.phase || "";
        (d.steps || []).forEach(renderStep);
        if (logEl && d.log) logEl.textContent = d.log;
        if (summaryEl && d.summary) summaryEl.textContent = d.summary;
        if (errorEl) {
          errorEl.textContent = d.error || "";
          errorEl.hidden = !d.error;
        }
        Object.keys(d.result || {}).forEach((k) => {
          const el = document.querySelector('[data-count="' + k + '"]');
          if (el) el.textContent = d.result[k];
        });
        if (d.terminal && !terminal) {
          terminal = true;
          if (doneActions) doneActions.hidden = false;
          window.PMS.toast(
            d.status === "done" ? "Sprint plan generated." : "Generation " + d.status + ".",
            d.status === "done" ? "success" : "error"
          );
        }
        if (d.terminal) clearInterval(timer);
      })
      .catch(() => {});
  }

  const timer = setInterval(tick, 2500);
  if (!terminal) tick();
})();
