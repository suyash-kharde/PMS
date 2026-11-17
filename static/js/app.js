/* shared helpers: theme, sidebar, confirms, csrf, inline patching */
(function () {
  "use strict";

  window.PMS = {
    csrf: function () {
      const m = document.cookie.match(/(?:^|;\s*)planwise_csrftoken=([^;]+)/);
      return m ? m[1] : "";
    },
    post: function (url, data) {
      return fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": window.PMS.csrf() },
        body: JSON.stringify(data || {}),
      }).then(async (r) => {
        const payload = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(payload.error || r.statusText);
        return payload;
      });
    },
    toast: function (text, kind) {
      kind = kind || "success";
      const el = document.createElement("div");
      el.className = "alert alert-" + kind;
      el.style.cssText =
        "position:fixed;right:16px;bottom:16px;z-index:99;max-width:320px;display:flex;align-items:flex-start;gap:10px";
      const msg = document.createElement("span");
      msg.style.cssText = "flex:1";
      msg.textContent = text;
      const closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.setAttribute("aria-label", "Close");
      closeBtn.textContent = "✕";
      closeBtn.style.cssText =
        "background:none;border:0;padding:0;margin:0;cursor:pointer;color:inherit;font-size:13px;line-height:1.4;flex:0 0 auto";
      el.append(msg, closeBtn);
      document.body.appendChild(el);
      const remove = () => el.remove();
      closeBtn.addEventListener("click", remove);
      // errors stay up until dismissed — everything else still clears itself, unchanged
      if (kind !== "error" && kind !== "danger") setTimeout(remove, 3200);
    },
  };

  // sidebar (mobile drawer + desktop persistence)
  document.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-toggle-sidebar]");
    if (toggle) {
      document.body.classList.toggle("nav-open");
      return;
    }
    const theme = e.target.closest("[data-toggle-theme]");
    if (theme) {
      const root = document.documentElement;
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      document.cookie = "theme=" + next + ";path=/;max-age=31536000;samesite=lax";
      return;
    }
    const alertClose = e.target.closest("[data-alert-close]");
    if (alertClose) {
      alertClose.closest(".alert").remove();
    }
  });

  // destructive form guard
  document.addEventListener("submit", (e) => {
    const msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // full-page loading overlay for actions that take a few seconds (e.g. an SSH round-trip
  // to a VM) — shown right up until the page navigates away on the form's normal POST/redirect
  document.addEventListener("submit", (e) => {
    if (e.defaultPrevented) return; // a data-confirm on the same form was just declined
    const msg = e.target.getAttribute("data-loading");
    if (!msg) return;
    const btn = e.target.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    const overlay = document.createElement("div");
    overlay.className = "loading-overlay";
    overlay.innerHTML = '<div class="loading-box"><span class="spinner"></span><span>' + msg + "</span></div>";
    document.body.appendChild(overlay);
  });

  // inline field patching (task detail sidebar)
  const patchRoot = document.querySelector("[data-task-patch]");
  if (patchRoot) {
    const url = patchRoot.getAttribute("data-task-patch");
    const status = patchRoot.querySelector("[data-patch-status]");
    // reload once every pending save is done, so the rest of the page shows the new values too
    let pending = 0, savedSomething = false;
    patchRoot.querySelectorAll("[data-patch]").forEach((input) => {
      input.addEventListener("change", () => {
        const body = {};
        body[input.getAttribute("data-patch")] = input.value;
        pending += 1;
        window.PMS.post(url, body)
          .then(() => { savedSomething = true; if (status) status.textContent = "Saved, refreshing…"; })
          .catch((err) => { if (status) status.textContent = "Error: " + err.message; })
          .finally(() => {
            pending -= 1;
            if (!pending && savedSomething) window.location.reload();
          });
      });
    });
  }

  // Back / Forward can show a page the browser kept in memory exactly as you left it, which hides what you
  // changed since (an assignee set on the ticket page still reads "Unassigned" on the list). Reload such a page.
  // A page that wants to step over itself on Back (the project edit page) stops this event first.
  window.addEventListener("pageshow", (e) => { if (e.persisted) window.location.reload(); });

  // keyboard: "/" focuses search
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) {
      const box = document.querySelector(".search input");
      if (box) { e.preventDefault(); box.focus(); }
    }
  });
})();
