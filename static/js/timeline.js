/* timeline: drag-and-drop upload + live polling of entries still being processed */
(function () {
  "use strict";

  // ---------------------------------------------------------------- drop zone
  const zone = document.getElementById("dropZone");
  const form = document.getElementById("entryForm");
  if (zone && form) {
    const input = form.querySelector('input[type="file"]');
    const preview = zone.querySelector(".drop-preview");

    ["dragenter", "dragover"].forEach((type) =>
      zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add("drag-over"); })
    );
    ["dragleave", "drop"].forEach((type) =>
      zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.remove("drag-over"); })
    );
    zone.addEventListener("drop", (e) => {
      const file = e.dataTransfer.files[0];
      if (!file) return;
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      showFile(file);
      const title = form.querySelector('input[name="title"]');
      if (title && !title.value) title.value = file.name.replace(/\.[^.]+$/, "");
      const kind = form.querySelector('select[name="kind"]');
      if (kind && /\.(wav|mp3|m4a|ogg|opus|flac|webm|aac|mp4)$/i.test(file.name)) kind.value = "audio";
    });
    input.addEventListener("change", () => input.files[0] && showFile(input.files[0]));

    function showFile(file) {
      preview.hidden = false;
      preview.innerHTML = "";
      const line = document.createElement("div");
      line.className = "chip";
      line.textContent = file.name + " · " + (file.size / 1048576).toFixed(2) + " MB";
      preview.appendChild(line);
      if (/^audio\//.test(file.type)) {
        const player = document.createElement("audio");
        player.controls = true;
        player.src = URL.createObjectURL(file);
        preview.appendChild(player);
      }
    }
  }

  // ---------------------------------------------------------------- live status
  const list = document.getElementById("timeline");
  if (!list) return;
  const busy = () =>
    Array.from(list.querySelectorAll(".tl-item")).filter((item) => {
      const badge = item.querySelector("[data-status]");
      return badge && /Queued|Processing/i.test(badge.textContent);
    });

  function poll() {
    const pending = busy();
    if (!pending.length) return;
    pending.forEach((item) => {
      fetch("/api/entries/" + item.dataset.entry + "/status/")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!data) return;
          const badge = item.querySelector("[data-status]");
          if (badge && badge.textContent.trim() !== data.status_label) {
            badge.textContent = data.status_label;
            badge.className = "badge status-" + data.status;
            if (data.status === "ready" || data.status === "awaiting_speakers") {
              window.PMS.toast("Entry " + data.id + ": " + data.status_label);
              setTimeout(() => window.location.reload(), 1200);
            }
          }
        })
        .catch(() => {});
    });
  }

  if (busy().length) setInterval(poll, 4000);
})();
