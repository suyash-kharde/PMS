/* Design (Penpot) tab: copy buttons, poll while provisioning */
(function () {
  "use strict";

  document.addEventListener("click", function (e) {
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      e.preventDefault();
      navigator.clipboard.writeText(copy.getAttribute("data-copy")).then(
        function () { window.PMS && window.PMS.toast("Copied to clipboard"); },
        function () { window.PMS && window.PMS.toast("Could not copy — select and copy manually", "danger"); }
      );
      return;
    }
  });

  if (document.querySelector("[data-poll]")) {
    setTimeout(function () { window.location.reload(); }, 6000);
  }
})();
