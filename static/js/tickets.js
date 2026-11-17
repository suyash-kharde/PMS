/* tickets: inline detail patching, status board drag & drop, bug-field reveal */
(function () {
  "use strict";

  // ---- inline field patching on the ticket detail sidebar
  const patchRoot = document.querySelector("[data-ticket-patch]");
  if (patchRoot) {
    const url = patchRoot.getAttribute("data-ticket-patch");
    const status = patchRoot.querySelector("[data-patch-status]");
    const note = (msg) => { if (status) status.textContent = msg; };
    // A save changes more than the field itself (the history, the watchers, the header), so the page is
    // reloaded once every pending save is done; waiting keeps a second quick edit from being cut off.
    let pending = 0, savedSomething = false;
    patchRoot.querySelectorAll("[data-patch]").forEach((input) => {
      input.addEventListener("change", () => {
        const body = {};
        body[input.getAttribute("data-patch")] = input.value;
        pending += 1;
        window.PMS.post(url, body)
          .then((res) => {
            if (res.changed && res.changed.length) { savedSomething = true; note("Saved, refreshing…"); }
            else note("Saved " + new Date().toLocaleTimeString());
          })
          .catch((err) => note("Error: " + err.message))
          .finally(() => {
            pending -= 1;
            if (!pending && savedSomething) window.location.reload();
          });
      });
    });
  }

  // ---- bug / incident detail: open the <details> when the type warrants it
  const typeSelect = document.getElementById("id_type");
  const bugBox = document.querySelector(".tkt-bugfields");
  if (typeSelect && bugBox) {
    const sync = () => {
      if (["bug", "incident"].indexOf(typeSelect.value) !== -1) bugBox.open = true;
    };
    typeSelect.addEventListener("change", sync);
  }

  // ---- status board
  const board = document.getElementById("ticketBoard");
  if (!board || board.dataset.canMove !== "1") return;
  let dragged = null;

  board.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".tkt-card");
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  });
  board.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
    board.querySelectorAll(".drag-over").forEach((z) => z.classList.remove("drag-over"));
  });

  board.querySelectorAll("[data-dropzone]").forEach((zone) => {
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("drag-over");
      if (!dragged) return;
      const column = zone.closest(".column");
      const card = dragged;
      zone.appendChild(card);
      refreshCounts();
      window.PMS.post("/api/tickets/" + card.dataset.ticket + "/patch/", { status: column.dataset.status })
        .then(() => window.PMS.toast("Moved to " + column.querySelector("h3").textContent))
        .catch((err) => { window.PMS.toast(err.message, "error"); window.location.reload(); });
    });
  });

  function refreshCounts() {
    board.querySelectorAll(".column").forEach((col) => {
      col.querySelector(".count").textContent = col.querySelectorAll(".tkt-card").length;
    });
  }
})();
