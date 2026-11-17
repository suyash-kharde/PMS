/* progressive enhancement: turns select[data-search] into a filterable dropdown */
(function () {
  "use strict";

  function enhance(select) {
    const wrap = document.createElement("div");
    wrap.className = "search-select";
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add("search-select-native");
    select.tabIndex = -1;

    const input = document.createElement("input");
    input.type = "text";
    input.className = "search-select-input";
    input.autocomplete = "off";
    // a filter keeps its empty choice ("Anyone") as the first entry; with nothing picked the box shows it as its placeholder
    const keepBlank = select.dataset.keepBlank === "1";
    const blankOption = select.querySelector('option[value=""]');
    input.placeholder = select.dataset.placeholder || (keepBlank && blankOption ? blankOption.textContent : "Search…");
    wrap.appendChild(input);

    const list = document.createElement("div");
    list.className = "search-select-list";
    list.hidden = true;
    wrap.appendChild(list);

    // read live, so a page script that hides or disables options (e.g. the ticket form's Team filter) is respected
    function liveOptions() {
      return Array.from(select.options).map((o) => ({ value: o.value, label: o.textContent, off: o.hidden || o.disabled }));
    }

    function labelFor(value) {
      const hit = liveOptions().find((o) => o.value === value);
      return hit && hit.value !== "" ? hit.label : "";
    }

    function renderList(filter) {
      list.innerHTML = "";
      const q = filter.trim().toLowerCase();
      const matches = liveOptions().filter(
        (o) => (o.value !== "" || keepBlank) && !o.off && (!q || o.value === "" || o.label.toLowerCase().includes(q))
      );
      list._first = matches.length ? matches[0] : null;
      if (!matches.length) {
        const empty = document.createElement("div");
        empty.className = "search-select-empty";
        empty.textContent = "No matches";
        list.appendChild(empty);
        return;
      }
      matches.forEach((o) => {
        const item = document.createElement("div");
        item.className = "search-select-option" + (o.value === select.value ? " active" : "");
        item.textContent = o.label;
        item.title = o.label;  // full name on hover when a long one is cut short
        item.addEventListener("mousedown", (e) => {
          e.preventDefault();
          select.value = o.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          input.value = o.value === "" ? "" : o.label;
          close();
        });
        list.appendChild(item);
      });
    }

    function open() {
      renderList(input.value === labelFor(select.value) ? "" : input.value);
      list.hidden = false;
      wrap.classList.add("open");
    }
    function close() {
      list.hidden = true;
      wrap.classList.remove("open");
      // clearing the box and leaving it means "Anyone" again
      if (keepBlank && input.value.trim() === "" && select.value !== "") {
        select.value = "";
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      input.value = labelFor(select.value);
    }

    input.addEventListener("focus", open);
    input.addEventListener("input", open);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { close(); input.blur(); }
      if (e.key === "Enter") {
        e.preventDefault();
        const first = list._first;  // Enter picks the first match of what was typed
        if (first && !list.hidden) {
          select.value = first.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          close();
        }
      }
    });
    // a page script can fire "refresh" on the select after it changes the options or the selection
    select.addEventListener("refresh", () => {
      input.disabled = select.disabled;
      const blank = select.querySelector('option[value=""]');
      input.placeholder = select.disabled && blank ? blank.textContent : (select.dataset.placeholder || "Search…");
      if (list.hidden) input.value = labelFor(select.value);
    });
    document.addEventListener("click", (e) => {
      if (!wrap.contains(e.target)) close();
    });

    input.value = labelFor(select.value);
  }

  document.querySelectorAll("select[data-search]").forEach(enhance);
})();
