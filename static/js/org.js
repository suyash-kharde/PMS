/* hierarchy renderer: indented outline (file-explorer style) for people,
   teams and roles — one row per node, nested under its parent, with a
   disclosure triangle to expand/collapse a branch. Same three datasets and
   the same People/Teams/Roles switcher and click-through as before. */
(function () {
  "use strict";
  const host = document.getElementById("orgChart");
  if (!host) return;

  const sets = {
    people: JSON.parse(host.dataset.people || "[]"),
    teams: JSON.parse(host.dataset.teams || "[]"),
    roles: JSON.parse(host.dataset.roles || "[]"),
  };

  let view = "people";
  const collapsed = new Set(); // node ids collapsed in the *current* view

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function rowLabel(kind, n) {
    if (kind === "people") {
      return (
        '<span class="avatar avatar-xs" style="--c:' + (n.color || "#6366f1") + '">' + esc(n.initials) + "</span>" +
        "<strong>" + esc(n.name) + "</strong>" +
        '<span class="org-meta">' + esc(n.title || n.role) + (n.team ? " · " + esc(n.team) : "") + "</span>"
      );
    }
    if (kind === "teams") {
      return (
        '<span class="org-dot" style="--c:' + (n.color || "#6366f1") + '"></span>' +
        "<strong>" + esc(n.name) + "</strong>" +
        '<span class="org-meta">' + (n.lead ? "lead " + esc(n.lead) : "no lead") + "</span>"
      );
    }
    return (
      '<span class="org-dot" style="--c:' + (n.color || "#6366f1") + '"></span>' +
      "<strong>" + esc(n.name) + "</strong>" +
      '<span class="org-meta">rank ' + n.rank + " · " + n.users + " user" + (n.users === 1 ? "" : "s") + "</span>"
    );
  }

  function renderTree() {
    const nodes = sets[view];
    const byId = new Set(nodes.map((n) => n.id));
    const byParent = {};
    nodes.forEach((n) => {
      const parent = n.parent && byId.has(n.parent) ? n.parent : "root";
      (byParent[parent] = byParent[parent] || []).push(n);
    });

    function branch(parentId) {
      const kids = byParent[parentId] || [];
      if (!kids.length) return "";
      return (
        "<ul>" +
        kids
          .map((n) => {
            const hasKids = !!(byParent[n.id] && byParent[n.id].length);
            const isCollapsed = collapsed.has(n.id);
            const marker = hasKids
              ? '<button type="button" class="org-toggle" data-toggle="' + n.id + '" aria-label="' +
                (isCollapsed ? "Expand" : "Collapse") + ' branch" aria-expanded="' + (!isCollapsed) + '">▸</button>'
              : '<span class="org-leaf">•</span>';
            const rowOpen = n.url ? '<a class="org-row" href="' + n.url + '"' : '<div class="org-row"';
            const rowClose = n.url ? "</a>" : "</div>";
            return (
              '<li class="' + (isCollapsed ? "is-collapsed" : "") + '" data-node="' + n.id + '">' +
                '<div class="org-row-wrap">' +
                  marker +
                  rowOpen + ">" + rowLabel(view, n) + rowClose +
                "</div>" +
                (hasKids && !isCollapsed ? branch(n.id) : "") +
              "</li>"
            );
          })
          .join("") +
        "</ul>"
      );
    }

    host.innerHTML = branch("root") || '<p class="empty">Nothing to show.</p>';
  }

  function switchView(kind) {
    view = kind;
    collapsed.clear();
    renderTree();
  }

  // ---- expand / collapse -------------------------------------------------
  host.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-toggle]");
    if (!btn) return; // otherwise let the row's own <a href> navigate as before
    e.preventDefault();
    e.stopPropagation();
    const id = Number(btn.dataset.toggle);
    if (collapsed.has(id)) collapsed.delete(id);
    else collapsed.add(id);
    renderTree();
  });

  // ---- view switcher (unchanged behaviour) --------------------------------
  const switcher = document.getElementById("orgSwitch");
  if (switcher) {
    switcher.addEventListener("click", (e) => {
      const link = e.target.closest("[data-view]");
      if (!link) return;
      e.preventDefault();
      switcher.querySelectorAll("a").forEach((a) => a.classList.remove("on"));
      link.classList.add("on");
      switchView(link.dataset.view);
    });
  }

  switchView("people");
})();
