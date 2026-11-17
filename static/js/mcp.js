/* Claude Code / MCP tab: copy-to-clipboard buttons */
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    const el = e.target.closest("[data-copy]");
    if (!el) return;
    e.preventDefault();
    const text = el.getAttribute("data-copy");
    navigator.clipboard.writeText(text).then(
      function () { window.PMS && window.PMS.toast("Copied to clipboard"); },
      function () { window.PMS && window.PMS.toast("Could not copy — select and copy manually", "danger"); }
    );
  });
})();
