/* Planwise service worker — push receipt and notification clicks. */
self.addEventListener("install", (event) => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = { title: "Planwise", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Planwise";
  const options = {
    body: data.body || "",
    tag: data.tag || "planwise",
    renotify: true,
    icon: "/static/img/icon-192.png",
    badge: "/static/img/icon-192.png",
    data: { url: data.url || "/notifications/", id: data.id || null },
    actions: [{ action: "open", title: "Open" }],
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/notifications/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ("focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
