(function () {
  "use strict";
  const card = document.getElementById("chatCard");
  if (!card) return;

  const log = document.getElementById("chatLog");
  const form = document.getElementById("chatForm");
  const status = document.getElementById("chatStatus");
  const reindexBtn = document.getElementById("reindexBtn");
  const clearChatBtn = document.getElementById("clearChatBtn");
  const toggleRag = document.getElementById("toggleRag");
  const toggleSql = document.getElementById("toggleSql");
  const projectId = card.dataset.project;
  const userColor = card.dataset.userColor || "#64748b";
  const userInitials = card.dataset.userInitials || "?";

  function renderMarkdown(raw) {
    const html = window.marked.parse(raw, { breaks: true });
    return window.DOMPurify.sanitize(html);
  }

  // history rendered by Django as escaped plain text — render it as markdown once, on load
  log.querySelectorAll(".chat-bubble[data-raw]").forEach((el) => {
    const raw = el.textContent;
    el.innerHTML = renderMarkdown(raw);
    el.removeAttribute("data-raw");
  });

  const prefsKey = "rag_chat_tools:" + projectId;
  (function restorePrefs() {
    try {
      const saved = JSON.parse(localStorage.getItem(prefsKey) || "{}");
      if ("use_rag" in saved) toggleRag.checked = saved.use_rag;
      if ("use_sql" in saved) toggleSql.checked = saved.use_sql;
    } catch (e) { /* ignore a corrupt or missing preference */ }
  })();
  function savePrefs() {
    localStorage.setItem(prefsKey, JSON.stringify({ use_rag: toggleRag.checked, use_sql: toggleSql.checked }));
  }
  toggleRag.addEventListener("change", savePrefs);
  toggleSql.addEventListener("change", savePrefs);

  function scrollToBottom() {
    log.scrollTop = log.scrollHeight;
  }

  function addMessage(role, rawText) {
    const empty = log.querySelector(".empty");
    if (empty) empty.remove();

    const div = document.createElement("div");
    div.className = "chat-msg chat-" + role;

    const avatar = document.createElement("span");
    avatar.className = "avatar avatar-xs chat-avatar";
    if (role === "user") {
      avatar.style.setProperty("--c", userColor);
      avatar.textContent = userInitials;
    } else {
      avatar.style.setProperty("--c", "#7c3aed");
      avatar.textContent = "✦";
    }

    const wrap = document.createElement("div");
    wrap.className = "chat-bubble-wrap";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.innerHTML = role === "user" ? renderMarkdown(rawText) : "";
    wrap.appendChild(bubble);

    div.appendChild(avatar);
    div.appendChild(wrap);
    log.appendChild(div);
    scrollToBottom();
    return { msgEl: div, bubbleEl: bubble, wrapEl: wrap };
  }

  function addCitesAndSql(wrapEl, sources, sqlUsed) {
    if (sources && sources.length) {
      const cites = document.createElement("div");
      cites.className = "chat-cites";
      sources.forEach((s) => {
        const chip = document.createElement("span");
        chip.className = "chip chat-cite";
        chip.textContent = s.source_type + " · " + s.label;
        cites.appendChild(chip);
      });
      wrapEl.appendChild(cites);
    }
    if (sqlUsed) {
      const details = document.createElement("details");
      details.className = "chat-sql";
      const summary = document.createElement("summary");
      summary.textContent = "SQL used";
      const pre = document.createElement("pre");
      pre.textContent = sqlUsed;
      details.appendChild(summary);
      details.appendChild(pre);
      wrapEl.appendChild(details);
    }
  }

  scrollToBottom();

  // --- WebSocket, with backoff reconnect ---
  let socket = null;
  let reconnectDelay = 1000;
  let pending = null; // {bubbleEl, wrapEl, raw}
  let intentionalClose = false;

  function setStatus(state, label) {
    status.className = "chat-status chat-status-" + state;
    status.innerHTML = '<i class="chat-dot"></i> ' + label;
  }

  function wsUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + window.location.host + "/ws/projects/" + projectId + "/chat/";
  }

  function connect() {
    setStatus("connecting", "Connecting…");
    socket = new WebSocket(wsUrl());

    socket.addEventListener("open", () => {
      reconnectDelay = 1000;
      setStatus("connected", "Connected");
      form.querySelector("button").disabled = false;
    });

    socket.addEventListener("message", (e) => {
      const event = JSON.parse(e.data);
      if (!pending) return;
      if (event.type === "delta") {
        pending.raw += event.text;
        pending.bubbleEl.innerHTML = renderMarkdown(pending.raw);
        scrollToBottom();
      } else if (event.type === "done") {
        pending.msgEl.classList.remove("chat-pending");
        addCitesAndSql(pending.wrapEl, event.sources, event.sql_used);
        pending = null;
        form.querySelector("input").disabled = false;
        form.querySelector("input").focus();
      } else if (event.type === "error") {
        pending.msgEl.classList.remove("chat-pending");
        if (!pending.raw) pending.bubbleEl.innerHTML = renderMarkdown("_Error: " + event.error + "_");
        pending = null;
        form.querySelector("input").disabled = false;
      }
      scrollToBottom();
    });

    socket.addEventListener("close", () => {
      form.querySelector("button").disabled = true;
      if (intentionalClose) return;
      setStatus("disconnected", "Reconnecting…");
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.6, 15000);
    });

    socket.addEventListener("error", () => socket.close());
  }
  connect();
  window.addEventListener("beforeunload", () => { intentionalClose = true; });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      window.PMS.toast("Not connected yet — try again in a moment.", "error");
      return;
    }
    const input = form.querySelector("input[name=question]");
    const question = input.value.trim();
    if (!question) return;

    addMessage("user", question);
    input.value = "";
    input.disabled = true;

    const { msgEl, bubbleEl, wrapEl } = addMessage("assistant", "");
    msgEl.classList.add("chat-pending");
    pending = { msgEl, bubbleEl, wrapEl, raw: "" };

    socket.send(JSON.stringify({ question, use_rag: toggleRag.checked, use_sql: toggleSql.checked }));
  });

  if (reindexBtn) {
    reindexBtn.addEventListener("click", () => {
      reindexBtn.disabled = true;
      const original = reindexBtn.textContent;
      reindexBtn.textContent = "Refreshing…";
      window.PMS.post(reindexBtn.dataset.url, {})
        .then((res) => window.PMS.toast(`Index refreshed — ${res.chunks} chunks.`))
        .catch((err) => window.PMS.toast(err.message, "error"))
        .finally(() => {
          reindexBtn.disabled = false;
          reindexBtn.textContent = original;
        });
    });
  }

  if (clearChatBtn) {
    clearChatBtn.addEventListener("click", () => {
      if (!window.confirm("Clear this conversation? This can't be undone.")) return;
      clearChatBtn.disabled = true;
      window.PMS.post(clearChatBtn.dataset.url, {})
        .then(() => {
          pending = null;
          log.innerHTML = '<p class="empty">Ask a question about this project — status, people, what was decided, anything in the BRD.</p>';
          window.PMS.toast("Chat cleared.");
        })
        .catch((err) => window.PMS.toast(err.message, "error"))
        .finally(() => { clearChatBtn.disabled = false; });
    });
  }
})();
