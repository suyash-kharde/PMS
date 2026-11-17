(function () {
  "use strict";

  const TOOLBAR = [
    { icon: "B", title: "Bold (Ctrl+B)", wrap: "**" },
    { icon: "I", title: "Italic (Ctrl+I)", wrap: "_" },
    { icon: "S", title: "Strikethrough", wrap: "~~" },
    { sep: true },
    { icon: "H1", title: "Heading 1", line: "# " },
    { icon: "H2", title: "Heading 2", line: "## " },
    { icon: "H3", title: "Heading 3", line: "### " },
    { sep: true },
    { icon: "❝", title: "Quote", line: "> " },
    { icon: "•", title: "Bullet list", line: "- " },
    { icon: "1.", title: "Numbered list", line: "1. " },
    { icon: "☑", title: "Checklist", line: "- [ ] " },
    { sep: true },
    { icon: "</>", title: "Code block", block: "```" },
    { icon: "🔗", title: "Link", link: true },
    { icon: "―", title: "Horizontal rule", insert: "\n\n---\n\n" },
  ];

  function renderMarkdown(text) {
    if (!window.marked) return (text || "").replace(/</g, "&lt;");
    const html = window.marked.parse(text || "", { breaks: true, gfm: true });
    return window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
  }

  function applyToLines(textarea, prefix) {
    const { selectionStart, selectionEnd, value } = textarea;
    const lineStart = value.lastIndexOf("\n", selectionStart - 1) + 1;
    let lineEnd = value.indexOf("\n", selectionEnd);
    if (lineEnd === -1) lineEnd = value.length;
    const block = value.slice(lineStart, lineEnd);
    const lines = block.split("\n").map((l) => (l.startsWith(prefix) ? l.slice(prefix.length) : prefix + l));
    const next = lines.join("\n");
    textarea.setRangeText(next, lineStart, lineEnd, "select");
  }

  function wrapSelection(textarea, wrap) {
    const { selectionStart, selectionEnd, value } = textarea;
    const selected = value.slice(selectionStart, selectionEnd) || "text";
    textarea.setRangeText(wrap + selected + wrap, selectionStart, selectionEnd, "select");
  }

  function insertBlock(textarea, fence) {
    const { selectionStart, selectionEnd, value } = textarea;
    const selected = value.slice(selectionStart, selectionEnd) || "code here";
    textarea.setRangeText(`${fence}\n${selected}\n${fence}`, selectionStart, selectionEnd, "select");
  }

  function insertLink(textarea) {
    const { selectionStart, selectionEnd, value } = textarea;
    const selected = value.slice(selectionStart, selectionEnd) || "link text";
    textarea.setRangeText(`[${selected}](https://)`, selectionStart, selectionEnd, "select");
  }

  function init(root) {
    const textarea = root.querySelector("textarea");
    if (!textarea) return;

    const toolbar = document.createElement("div");
    toolbar.className = "md-toolbar";
    TOOLBAR.forEach((btn) => {
      if (btn.sep) {
        const sep = document.createElement("span");
        sep.className = "md-toolbar-sep";
        toolbar.appendChild(sep);
        return;
      }
      const el = document.createElement("button");
      el.type = "button";
      el.className = "md-toolbar-btn";
      el.textContent = btn.icon;
      el.title = btn.title;
      el.addEventListener("click", () => {
        textarea.focus();
        if (btn.wrap) wrapSelection(textarea, btn.wrap);
        else if (btn.line) applyToLines(textarea, btn.line);
        else if (btn.block) insertBlock(textarea, btn.block);
        else if (btn.link) insertLink(textarea);
        else if (btn.insert) textarea.setRangeText(btn.insert, textarea.selectionStart, textarea.selectionEnd, "end");
        textarea.dispatchEvent(new Event("input"));
      });
      toolbar.appendChild(el);
    });

    const previewToggle = document.createElement("button");
    previewToggle.type = "button";
    previewToggle.className = "md-toolbar-btn md-preview-toggle";
    previewToggle.textContent = "Preview";
    toolbar.appendChild(previewToggle);

    root.insertBefore(toolbar, textarea);

    const split = document.createElement("div");
    split.className = "md-split";
    textarea.parentNode.insertBefore(split, textarea);
    split.appendChild(textarea);
    textarea.classList.add("md-source");

    const preview = document.createElement("article");
    preview.className = "prose markdown md-preview";
    split.appendChild(preview);

    const update = () => { preview.innerHTML = renderMarkdown(textarea.value); };
    update();
    textarea.addEventListener("input", update);

    let showingPreview = true;
    const syncView = () => {
      split.classList.toggle("md-preview-hidden", !showingPreview);
      previewToggle.classList.toggle("on", showingPreview);
    };
    previewToggle.addEventListener("click", () => {
      showingPreview = !showingPreview;
      syncView();
    });
    syncView();

    textarea.addEventListener("keydown", (e) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "b") { e.preventDefault(); wrapSelection(textarea, "**"); textarea.dispatchEvent(new Event("input")); }
      if (mod && e.key.toLowerCase() === "i") { e.preventDefault(); wrapSelection(textarea, "_"); textarea.dispatchEvent(new Event("input")); }
      if (e.key === "Tab") { e.preventDefault(); textarea.setRangeText("  ", textarea.selectionStart, textarea.selectionEnd, "end"); }
    });
  }

  document.querySelectorAll("[data-md-editor]").forEach(init);
})();
