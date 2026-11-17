// AI queue: keep "#3 in line · usually ready within 5 min" up to date, and reload the page
// when a job the page is waiting for finishes.
(function () {
  var items = Array.prototype.slice.call(document.querySelectorAll("[data-job-url]"));
  if (!items.length) return;
  var timer = setInterval(poll, 5000);
  function poll() {
    items = items.filter(function (el) { return el.isConnected; });
    if (!items.length) { clearInterval(timer); return; }
    items.forEach(function (el) {
      fetch(el.dataset.jobUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) return;
          var text = el.querySelector("[data-job-text]");
          if (text) text.textContent = d.text;
          if (!d.active) {
            clearInterval(timer);
            if (window.PMS && window.PMS.toast) {
              window.PMS.toast(d.label + ": " + d.status_label, d.status === "done" ? "success" : "error");
            }
            setTimeout(function () { window.location.reload(); }, 1500);
          }
        })
        .catch(function () {});
    });
  }
})();
