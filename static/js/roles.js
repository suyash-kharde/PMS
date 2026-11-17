/* role capability matrix — radios save immediately */
(function () {
  "use strict";
  const matrix = document.getElementById("matrix");
  if (!matrix) return;
  const url = matrix.dataset.url;

  matrix.addEventListener("change", (e) => {
    const input = e.target.closest('input[type="radio"]');
    if (!input) return;
    const row = input.closest("tr");
    window.PMS.post(url, { capability: row.dataset.cap, scope: input.value })
      .then(() => {
        row.style.transition = "background .3s";
        row.style.background = "color-mix(in srgb, var(--success) 14%, transparent)";
        setTimeout(() => (row.style.background = ""), 600);
      })
      .catch((err) => window.PMS.toast(err.message, "error"));
  });

  // search box: filters the permission rows by label or code; a category with no match is hidden
  const search = document.getElementById("capSearch");
  if (search) {
    const noMatch = document.getElementById("capNoMatch");
    const filter = () => {
      const q = search.value.trim().toLowerCase();
      let shown = 0;
      matrix.querySelectorAll("table.matrix").forEach((table) => {
        let any = false;
        table.querySelectorAll("tbody tr").forEach((row) => {
          const hit = !q || row.cells[0].textContent.toLowerCase().includes(q);
          row.hidden = !hit;
          if (hit) any = true;
        });
        table.hidden = !any;
        const heading = table.previousElementSibling;
        if (heading && heading.classList.contains("sub")) heading.hidden = !any;
        if (any) shown += 1;
      });
      if (noMatch) noMatch.hidden = shown > 0;
    };
    search.addEventListener("input", filter);
  }
})();
