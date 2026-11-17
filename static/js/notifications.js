/* Notification inbox: before following a notification to its target, probe that URL first.
   A deleted resource 404s like anything else — instead of navigating into that error page, mark
   the notification read (same as a normal open) and show a dismissible toast in its place. */
(function () {
  "use strict";

  function markRead(url) {
    if (!url) return Promise.resolve();
    return fetch(url, {
      method: "POST",
      headers: { "X-CSRFToken": window.PMS.csrf(), "X-Requested-With": "fetch" },
    }).catch(() => {});
  }

  function isReachable(url) {
    if (!url) return Promise.resolve(false);
    return fetch(url, { method: "GET", credentials: "same-origin" })
      .then((r) => r.ok)
      .catch(() => false);
  }

  function decrementBy1(el) {
    if (!el) return;
    const match = el.textContent.match(/\d+/);
    if (!match) return;
    el.textContent = el.textContent.replace(/\d+/, String(Math.max(0, parseInt(match[0], 10) - 1)));
  }

  function markRowReadInPlace(row) {
    if (!row) return;
    row.classList.remove("unread");
    const markForm = row.querySelector("[data-notification-mark-only]");
    if (markForm) markForm.remove();
    decrementBy1(document.querySelector(".page-head .muted"));
    const badge = document.getElementById("bellBadge");
    if (badge) {
      decrementBy1(badge);
      if ((parseInt(badge.textContent, 10) || 0) <= 0) badge.hidden = true;
    }
  }

  function openOrToast(url, row) {
    isReachable(url).then((ok) => {
      if (ok) {
        window.location.href = url;
      } else {
        markRowReadInPlace(row);
        window.PMS.toast("This notification's linked item no longer exists.", "error");
      }
    });
  }

  document.querySelectorAll("form[data-notification-open]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const url = form.getAttribute("data-url");
      const row = form.closest(".list-row");
      markRead(form.action).then(() => openOrToast(url, row));
    });
  });

  document.querySelectorAll("a[data-notification-open]").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      openOrToast(link.getAttribute("data-url"), link.closest(".list-row"));
    });
  });
})();
