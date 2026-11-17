/* kanban: HTML5 drag & drop on pointer devices, tap-to-move fallback on touch */
(function () {
  "use strict";
  const board = document.getElementById("board");
  if (!board) return;

  const moveUrl = board.dataset.moveUrl;
  const createUrl = board.dataset.createUrl;
  const projectId = board.dataset.project;
  const canMove = board.dataset.canMove === "1";
  let dragged = null;

  function orderOf(zone) {
    return Array.from(zone.querySelectorAll(".task-card")).map((c) => c.dataset.task);
  }

  function commit(card, zone) {
    const column = zone.closest(".column");
    return window.PMS.post(moveUrl, {
      task: card.dataset.task,
      state: column.dataset.state,
      order: orderOf(zone),
    }).then(() => refreshCounts())
      .catch((err) => { window.PMS.toast(err.message, "error"); window.location.reload(); });
  }

  function refreshCounts() {
    board.querySelectorAll(".column").forEach((col) => {
      const n = col.querySelectorAll(".task-card").length;
      const badge = col.querySelector(".count");
      badge.textContent = badge.textContent.includes("/")
        ? n + "/" + badge.textContent.split("/")[1]
        : n;
    });
  }

  if (canMove) {
    board.addEventListener("dragstart", (e) => {
      const card = e.target.closest(".task-card");
      if (!card) return;
      dragged = card;
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", card.dataset.task);
    });

    board.addEventListener("dragend", () => {
      if (dragged) dragged.classList.remove("dragging");
      dragged = null;
      board.querySelectorAll(".drag-over").forEach((z) => z.classList.remove("drag-over"));
    });

    board.querySelectorAll("[data-dropzone]").forEach((zone) => {
      zone.addEventListener("dragover", (e) => {
        e.preventDefault();
        zone.classList.add("drag-over");
        const after = insertionPoint(zone, e.clientY);
        if (!dragged) return;
        if (after == null) zone.appendChild(dragged);
        else zone.insertBefore(dragged, after);
      });
      zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
      zone.addEventListener("drop", (e) => {
        e.preventDefault();
        zone.classList.remove("drag-over");
        if (dragged) commit(dragged, zone);
      });
    });

    // touch fallback: long-press a card, then tap a column
    let picked = null;
    board.addEventListener("click", (e) => {
      const card = e.target.closest(".task-card");
      const column = e.target.closest(".column");
      if (card && e.target.tagName !== "A") {
        picked = picked === card ? null : card;
        board.querySelectorAll(".task-card").forEach((c) => (c.style.outline = ""));
        if (picked) { picked.style.outline = "2px solid var(--primary)"; window.PMS.toast("Now tap a column header to move"); }
        return;
      }
      if (picked && column && e.target.closest("header")) {
        const zone = column.querySelector("[data-dropzone]");
        zone.appendChild(picked);
        commit(picked, zone);
        picked.style.outline = "";
        picked = null;
      }
    });
  }

  function insertionPoint(zone, y) {
    const cards = Array.from(zone.querySelectorAll(".task-card:not(.dragging)"));
    return cards.find((c) => {
      const box = c.getBoundingClientRect();
      return y < box.top + box.height / 2;
    }) || null;
  }

  // quick add
  board.querySelectorAll(".quick-add").forEach((form) => {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = form.querySelector("input");
      const title = input.value.trim();
      if (!title) return;
      window.PMS.post(createUrl, { project: projectId, title: title, state: form.dataset.state })
        .then((res) => {
          input.value = "";
          const zone = form.closest(".column").querySelector("[data-dropzone]");
          const card = document.createElement("article");
          card.className = "task-card";
          card.draggable = true;
          card.dataset.task = res.task.id;
          card.innerHTML =
            '<div class="task-top"><span class="prio prio-3"></span>' +
            '<a class="task-title" href="' + res.task.url + '"></a></div>';
          card.querySelector("a").textContent = res.task.title;
          zone.appendChild(card);
          refreshCounts();
        })
        .catch((err) => window.PMS.toast(err.message, "error"));
    });
  });
})();
