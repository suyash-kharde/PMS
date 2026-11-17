/* Live search for every GET filter form: results update while you type (no Filter / Search button).
 *
 * Applies to any <form method="get"> that contains an <input type="search" name="…">, except:
 *   - forms with onsubmit="return false" or data-live="off" (they filter in the page already)
 *   - the header search box, unless you are already on the search results page
 * Typing waits ~0.5 s after the last key, then reloads the results; the cursor stays in the box.
 * Selects, dates and checkboxes in the same form apply as soon as they change.
 */
(function () {
  "use strict";
  var KEY = "pms.liveSearch", DELAY = 450;

  function isLiveForm(form) {
    if ((form.getAttribute("method") || "get").toLowerCase() !== "get") return false;
    if (form.dataset.live === "off") return false;
    if (/return\s+false/.test(form.getAttribute("onsubmit") || "")) return false;
    if (!form.querySelector('input[type="search"][name]')) return false;
    if (form.classList.contains("search")) {  // header search: live only on the results page itself
      var target = new URL(form.getAttribute("action") || location.href, location.href);
      return target.pathname === location.pathname;
    }
    return true;
  }

  function go(form, field) {
    var url = new URL(form.getAttribute("action") || location.href, location.href);
    url.search = new URLSearchParams(new FormData(form)).toString();
    if (url.href === location.href) return;
    try {
      if (field) sessionStorage.setItem(KEY, JSON.stringify({
        path: url.pathname, name: field.name, pos: field.selectionStart || field.value.length,
      }));
    } catch (e) { /* storage blocked: focus just isn't restored */ }
    location.replace(url.href);  // replace: typing doesn't fill the Back history
  }

  document.querySelectorAll("form").forEach(function (form) {
    if (!isLiveForm(form)) return;
    form.classList.add("is-live");
    var timer = null;

    form.querySelectorAll('input[type="search"][name]').forEach(function (box) {
      box.setAttribute("autocomplete", "off");
      box.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () { go(form, box); }, DELAY);
      });
      box.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); clearTimeout(timer); go(form, box); }
      });
    });
    form.querySelectorAll("select, input[type=date], input[type=month], input[type=checkbox], input[type=radio]")
      .forEach(function (el) {
        el.addEventListener("change", function () { clearTimeout(timer); go(form, null); });
      });
    // the explicit Filter / Search / Show button is no longer needed (Reset links stay)
    form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (b) {
      b.classList.add("live-hidden");
    });
  });

  // after a live reload, put the cursor back where it was
  try {
    var saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
    sessionStorage.removeItem(KEY);
    if (saved && saved.path === location.pathname) {
      var box = document.querySelector('form.is-live input[type="search"][name="' + saved.name + '"]');
      if (box) {
        box.focus();
        var pos = Math.min(saved.pos, box.value.length);
        try { box.setSelectionRange(pos, pos); } catch (e) { /* not supported for this input */ }
      }
    }
  } catch (e) { /* storage blocked */ }
})();
