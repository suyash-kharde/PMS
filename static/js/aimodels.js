/* Test buttons on the AI models page: call the endpoint and show the result inline. */
(function () {
  "use strict";

  function render(target, data) {
    const ok = data.ok;
    const models = (data.models || []).map((m) => '<span class="chip">' + m + "</span>").join(" ");
    return (
      '<div class="alert ' + (ok ? "alert-success" : "alert-error") + '">' +
      "<strong>" + (ok ? "OK" : "Failed") + "</strong> · " + data.latency_ms + "ms · " +
      String(data.message).replace(/</g, "&lt;") +
      (models ? '<div class="small">' + models + "</div>" : "") +
      "</div>"
    );
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-test]");
    if (!button) return;
    event.preventDefault();

    const row = button.dataset.row;
    const box = document.getElementById("test-" + row);
    const cell = box ? (box.querySelector("td") || box) : null;
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Testing…";
    if (box) {
      box.hidden = false;
      cell.innerHTML = '<span class="muted small">Calling the endpoint…</span>';
    }

    window.PMS.post(button.dataset.test, { mode: button.dataset.mode || "connect" })
      .then((data) => {
        if (cell) cell.innerHTML = render(cell, data);
        else window.PMS.toast(data.message, data.ok ? "success" : "error");
      })
      .catch((err) => {
        if (cell) cell.innerHTML = '<div class="alert alert-error">' + err.message + "</div>";
        else window.PMS.toast(err.message, "error");
      })
      .finally(() => {
        button.disabled = false;
        button.textContent = label;
      });
  });
})();
