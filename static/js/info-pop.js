/* "i" buttons: hover shows the full text (title tooltip); click opens a small popover with it */
(function () {
  "use strict";
  let open = null;

  function place(btn, pop) {
    const gap = 6, margin = 8;
    const r = btn.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    // below the button, unless there is no room: then above it
    let top = r.bottom + gap;
    if (top + h > window.innerHeight - margin && r.top - gap - h >= margin) top = r.top - gap - h;
    top = Math.max(margin, Math.min(top, window.innerHeight - h - margin));
    const left = Math.max(margin, Math.min(r.left, window.innerWidth - w - margin));
    pop.style.top = top + "px";
    pop.style.left = left + "px";
  }

  function close() {
    if (!open) return;
    open.pop.remove();
    open.btn.setAttribute("aria-expanded", "false");
    open = null;
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".info-btn");
    if (!btn) { if (!e.target.closest(".info-pop")) close(); return; }
    e.preventDefault();
    e.stopPropagation();
    if (open && open.btn === btn) { close(); return; }
    close();

    const pop = document.createElement("div");
    pop.className = "info-pop";
    pop.setAttribute("role", "dialog");
    if (btn.dataset.infoLabel) {  // a small heading, only for buttons that ask for one (the Solved icon)
      const label = document.createElement("span");
      label.className = "info-pop-label";
      label.textContent = btn.dataset.infoLabel;
      pop.append(label);
    }
    const name = document.createElement("strong");
    name.textContent = btn.dataset.info;  // textContent: the text is never treated as HTML
    pop.append(name);
    if (btn.dataset.infoUrl) {
      const link = document.createElement("a");
      link.href = btn.dataset.infoUrl;
      link.textContent = "Open project";
      pop.append(link);
    }
    // attached to <body> and positioned on screen, so a scrolling table or card can never clip it
    document.body.appendChild(pop);
    place(btn, pop);
    btn.setAttribute("aria-expanded", "true");
    open = { btn, pop };
  });

  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  // the popup is fixed on screen, so close it when the page or any scrolling area moves under it
  window.addEventListener("scroll", close, true);
  window.addEventListener("resize", close);
})();
