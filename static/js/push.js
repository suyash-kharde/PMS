/* Web push: register the service worker, manage the subscription, poll the bell badge. */
(function () {
  "use strict";

  const bell = document.getElementById("bell");
  const badge = document.getElementById("bellBadge");
  const toggle = document.getElementById("pushToggle");
  const statusBox = document.getElementById("pushStatus");
  const testBtn = document.getElementById("pushTest");

  const supported = "serviceWorker" in navigator && "PushManager" in window;
  let registration = null;

  function setStatus(text, kind) {
    if (!statusBox) return;
    statusBox.textContent = text;
    statusBox.className = "push-status " + (kind || "");
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = window.atob(base64);
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }

  async function register() {
    if (!supported) return null;
    if (registration) return registration;
    registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    await navigator.serviceWorker.ready;
    return registration;
  }

  async function currentSubscription() {
    const reg = await register();
    return reg ? reg.pushManager.getSubscription() : null;
  }

  async function enable() {
    if (!supported) {
      setStatus("This browser does not support the Push API.", "err");
      return;
    }
    if (!window.isSecureContext) {
      setStatus("Push needs HTTPS. Open the site over https:// (or on localhost).", "err");
      return;
    }
    const info = await fetch("/api/push/public-key/").then((r) => r.json());
    if (!info.enabled) {
      setStatus("Server has no VAPID key configured.", "err");
      return;
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      setStatus("Permission " + permission + " — allow notifications in the browser to enable push.", "err");
      syncToggle();
      return;
    }
    const reg = await register();
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(info.publicKey),
      });
    }
    await window.PMS.post("/api/push/subscribe/", sub.toJSON());
    setStatus("Push enabled on this device.", "ok");
    syncToggle();
  }

  async function disable() {
    const sub = await currentSubscription();
    if (sub) {
      await window.PMS.post("/api/push/unsubscribe/", { endpoint: sub.endpoint }).catch(() => {});
      await sub.unsubscribe();
    }
    setStatus("Push disabled on this device.", "");
    syncToggle();
  }

  async function syncToggle() {
    if (!toggle) return;
    if (!supported) {
      toggle.textContent = "Not supported";
      toggle.disabled = true;
      return;
    }
    const sub = await currentSubscription();
    const on = Boolean(sub) && Notification.permission === "granted";
    toggle.textContent = on ? "Disable push on this device" : "Enable push on this device";
    toggle.dataset.on = on ? "1" : "0";
    if (statusBox && !statusBox.textContent) {
      setStatus(
        on ? "Push enabled on this device."
           : "Push is off. Permission: " + (window.Notification ? Notification.permission : "unavailable"),
        on ? "ok" : ""
      );
    }
  }

  if (toggle) {
    toggle.addEventListener("click", () => (toggle.dataset.on === "1" ? disable() : enable()));
    syncToggle();
  }

  if (testBtn) {
    testBtn.addEventListener("click", () => {
      window.PMS.post("/api/push/test/", {})
        .then((res) => {
          setStatus(
            res.pushed
              ? "Test notification sent to " + res.subscriptions + " device(s)."
              : "Saved to your inbox, but push failed: " + (res.error || "no subscription on this device"),
            res.pushed ? "ok" : "err"
          );
          refreshBadge();
        })
        .catch((err) => setStatus("Test failed: " + err.message, "err"));
    });
  }

  // ---------------------------------------------------------------- bell badge
  function refreshBadge() {
    if (!badge) return;
    fetch("/api/notifications/unread/")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        badge.textContent = data.unread > 99 ? "99+" : data.unread;
        badge.hidden = data.unread === 0;
        if (bell) bell.title = data.unread ? data.unread + " unread notifications" : "Notifications";
      })
      .catch(() => {});
  }

  if (badge) {
    refreshBadge();
    setInterval(refreshBadge, 30000);
    // a push arriving while the tab is open should bump the badge immediately
    if (supported) {
      navigator.serviceWorker.addEventListener("message", refreshBadge);
      register().catch(() => {});
    }
  }
})();
