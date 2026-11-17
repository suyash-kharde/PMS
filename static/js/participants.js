/* client side / our side: drag & drop on pointer devices, tap-to-move fallback on touch */
(function () {
  "use strict";
  const root = document.getElementById("participantSides");
  if (!root) return;

  const canMove = root.dataset.canMove === "1";
  if (!canMove) return;
  let dragged = null;

  function refreshCounts() {
    root.querySelectorAll("[data-dropzone]").forEach((zone) => {
      const section = zone.closest(".card");
      const badge = section.querySelector(".side-count");
      badge.textContent = zone.querySelectorAll(".participant").length;
    });
  }

  function commit(card, zone) {
    return window.PMS.post(card.dataset.moveUrl, { side: zone.dataset.side })
      .then(refreshCounts)
      .catch((err) => { window.PMS.toast(err.message, "error"); window.location.reload(); });
  }

  root.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".participant");
    if (!card || !card.dataset.moveUrl) return;
    dragged = card;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", card.dataset.moveUrl);
  });

  root.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
    root.querySelectorAll(".drag-over").forEach((z) => z.classList.remove("drag-over"));
  });

  root.querySelectorAll("[data-dropzone]").forEach((zone) => {
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("drag-over");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("drag-over");
      if (!dragged || dragged.closest("[data-dropzone]") === zone) return;
      const empty = zone.querySelector(".empty");
      if (empty) empty.remove();
      zone.appendChild(dragged);
      refreshCounts();
      commit(dragged, zone);
    });
  });

  // touch fallback: tap a card, then tap the other column
  let picked = null;
  root.addEventListener("click", (e) => {
    const card = e.target.closest(".participant");
    const zone = e.target.closest("[data-dropzone]");
    if (card && card.dataset.moveUrl && e.target.tagName !== "A" && e.target.tagName !== "BUTTON") {
      picked = picked === card ? null : card;
      root.querySelectorAll(".participant").forEach((c) => (c.style.outline = ""));
      if (picked) { picked.style.outline = "2px solid var(--primary)"; window.PMS.toast("Now tap the other column to move them"); }
      return;
    }
    if (picked && zone && zone !== picked.closest("[data-dropzone]")) {
      const empty = zone.querySelector(".empty");
      if (empty) empty.remove();
      zone.appendChild(picked);
      refreshCounts();
      commit(picked, zone);
      picked.style.outline = "";
      picked = null;
    }
  });
})();
