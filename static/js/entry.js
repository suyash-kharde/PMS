/* entry detail: speaker names propagate into the transcript as you type, live status poll */
(function () {
  "use strict";

  const table = document.querySelector(".speaker-table");
  if (table) {
    const rows = Array.from(table.querySelectorAll("tbody tr"));

    function labelOf(row) {
      const code = row.querySelector("code");
      return code ? code.textContent.trim() : "";
    }

    function paint(label, name) {
      document.querySelectorAll('.seg-who[data-label="' + label + '"]').forEach((el) => {
        el.textContent = name || label;
        el.classList.toggle("named", Boolean(name));
      });
    }

    rows.forEach((row) => {
      const label = labelOf(row);
      const nameInput = row.querySelector('input[name$="display_name"]');
      const userSelect = row.querySelector('select[name$="user"]');

      const selectedName = () => {
        if (!userSelect || userSelect.selectedIndex < 0) return "";
        const option = userSelect.options[userSelect.selectedIndex];
        // the option label carries position and side; data-name is the bare person
        return option.dataset.name || (userSelect.selectedIndex > 0 ? option.text : "");
      };

      const sync = () => {
        const name = (nameInput ? nameInput.value.trim() : "") || selectedName();
        paint(label, name);
      };
      if (nameInput) nameInput.addEventListener("input", sync);
      if (userSelect) {
        userSelect.addEventListener("change", () => {
          const name = selectedName();
          if (nameInput && !nameInput.value.trim() && name) nameInput.placeholder = name;
          const sideSelect = row.querySelector('select[name$="-side"]');
          const side = userSelect.options[userSelect.selectedIndex].dataset.side;
          if (sideSelect && side && !sideSelect.value) sideSelect.value = side;
          sync();
        });
      }
      sync();
    });

    // colour each speaker consistently across the transcript
    const palette = ["#6366f1", "#0ea5e9", "#f59e0b", "#22c55e", "#ec4899", "#8b5cf6"];
    const seen = {};
    document.querySelectorAll(".seg").forEach((seg) => {
      const label = seg.dataset.speaker;
      if (!(label in seen)) seen[label] = palette[Object.keys(seen).length % palette.length];
      seg.style.setProperty("--c", seen[label]);
    });
  }

  // ---------------------------------------------------------------- sample playback
  // One <audio> for the whole recording; buttons seek into it and stop at the end of a
  // range, so no per-speaker clips have to be cut on the server.
  const audio = document.getElementById("entryAudio");
  if (audio && audio.dataset.clipUrl) {
    // Playback goes through Web Audio rather than the <audio> element: the clips are short
    // and already cut to the right ranges, decoding gives exact start/stop, and it avoids
    // the element's buffering behaviour on long recordings.
    let context = null;
    let source = null;
    let owner = null;
    let token = 0;
    const cache = new Map();

    function reset() {
      token += 1;
      if (source) {
        try { source.onended = null; source.stop(); } catch (err) { /* already stopped */ }
        source = null;
      }
      if (owner) {
        owner.classList.remove("playing");
        owner.textContent = owner.dataset.label || owner.textContent;
      }
      owner = null;
      document.querySelectorAll(".seg.playing").forEach((el) => el.classList.remove("playing"));
    }

    async function clipBuffer(query) {
      if (cache.has(query)) return cache.get(query);
      const response = await fetch(audio.dataset.clipUrl + "?ranges=" + encodeURIComponent(query));
      if (!response.ok) throw new Error("clip request failed (" + response.status + ")");
      const buffer = await context.decodeAudioData(await response.arrayBuffer());
      cache.set(query, buffer);
      return buffer;
    }

    async function play(button, ranges, highlight) {
      reset();
      const mine = ++token;
      owner = button;
      button.dataset.label = button.dataset.label || button.textContent;
      button.classList.add("playing");
      const isSpeaker = button.classList.contains("play-speaker");
      if (isSpeaker) button.textContent = "… loading";
      if (highlight) {
        highlight.classList.add("playing");
        highlight.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }

      try {
        context = context || new (window.AudioContext || window.webkitAudioContext)();
        if (context.state === "suspended") await context.resume();
        const buffer = await clipBuffer(ranges.map((r) => r[0].toFixed(2) + "-" + r[1].toFixed(2)).join(","));
        if (mine !== token) return; // another button was pressed while this loaded
        source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        source.onended = () => { if (mine === token) reset(); };
        source.start();
        if (isSpeaker) button.textContent = "■ " + Math.round(buffer.duration) + "s";
      } catch (err) {
        window.PMS.toast("Could not play audio: " + err.message, "error");
        reset();
      }
    }

    document.addEventListener("click", (event) => {
      const speakerBtn = event.target.closest(".play-speaker");
      const segBtn = event.target.closest(".seg-play");
      if (!speakerBtn && !segBtn) return;
      event.preventDefault();

      const button = speakerBtn || segBtn;
      if (owner === button) {
        reset();
        return;
      }
      if (speakerBtn) {
        play(button, JSON.parse(button.dataset.ranges || "[]"), null);
      } else {
        play(button, [[parseFloat(button.dataset.start), parseFloat(button.dataset.end)]], button.closest(".seg"));
      }
    });
  }

  // poll while the entry is still being processed
  const badge = document.querySelector("[data-status]");
  const log = document.querySelector("[data-log]");
  if (!badge || !/Queued|Processing/i.test(badge.textContent)) return;
  const id = window.location.pathname.split("/").filter(Boolean).pop();
  const timer = setInterval(() => {
    fetch("/api/entries/" + id + "/status/")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        badge.textContent = data.status_label;
        badge.className = "badge status-" + data.status;
        if (log) log.textContent = data.log;
        if (!/queued|processing/.test(data.status)) {
          clearInterval(timer);
          window.location.reload();
        }
      })
      .catch(() => clearInterval(timer));
  }, 4000);
})();
