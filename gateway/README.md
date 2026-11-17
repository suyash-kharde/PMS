# Path Gateway

**One IP and one port in front of many web applications.**

A small reverse proxy written in Go (standard library only) that publishes web apps living on different machines and ports under a single address, each under its own path:

```
https://gateway/app1/          ->  http://172.16.16.38:442
https://gateway/app2/          ->  http://172.16.16.40:8080
https://gateway/crm/_api/      ->  https://crm.internal:8443/api      (the app's own API, called a "mount")
```

You manage the list of apps while the gateway runs (browser admin page or JSON API). The gateway also repairs the things that normally break when a web app is moved under a path prefix: absolute links, hard-coded API addresses, cookie paths, redirects, browser caching and shared browser storage.

> Last checked against the source code and the running system: 2026-10-07.
> Companion file: [GATEWAY-NOTES.md](GATEWAY-NOTES.md) is the detailed evidence log (what was tested, how, and what the results were).

---

## Contents

1. [What it is, and what it is not](#1-what-it-is-and-what-it-is-not)
2. [Quick start](#2-quick-start)
3. [Concepts](#3-concepts)
4. [How it is supposed to be used](#4-how-it-is-supposed-to-be-used)
5. [Configuration reference](#5-configuration-reference)
6. [Admin interface and API](#6-admin-interface-and-api)
7. [How a request flows through the gateway](#7-how-a-request-flows-through-the-gateway)
8. [Feature reference](#8-feature-reference)
9. [Integrating with another solution](#9-integrating-with-another-solution)
10. [Security model and hardening checklist](#10-security-model-and-hardening-checklist)
11. [Operations runbook](#11-operations-runbook)
12. [Troubleshooting](#12-troubleshooting)
13. [Limitations and things that are not implemented](#13-limitations-and-things-that-are-not-implemented)
14. [Verification record and field notes](#14-verification-record-and-field-notes)
15. [Source map and development](#15-source-map-and-development)

---

## 1. What it is, and what it is not

### The problem it solves

You have many web applications on different VMs and ports. Users (or a firewall) should only need to know **one address and port**. Nobody should have to know each app's internal routes, because the gateway only maps a prefix to a target and forwards everything after the prefix untouched.

### What it does

- **Routes by prefix.** `/app1/anything` goes to the app registered as `app1`, with `/app1` removed, so the app sees `/anything`.
- **Is managed at runtime.** Add, change and remove apps without restarting. The list survives restarts.
- **Repairs apps that were not written for a prefix.** Rewrites links in HTML, CSS and JavaScript, patches browser APIs with a small injected script, fixes redirects and cookie paths.
- **Exposes an app's separate API server on the same origin** (a "mount"), which removes mixed-content and CORS problems.
- **Passes uploads, downloads, range requests and WebSockets** through without altering them.
- **Can isolate browser storage per app** and **can coordinate token refresh** for backends with very short-lived tokens.

### What it is not

| It is not | Because |
|---|---|
| A load balancer | Each app or mount has exactly one target. No health checks, no failover. |
| An authentication layer for the proxied apps | Anyone who can reach the gateway can reach every registered app. Put authentication in front if needed (section 9.5). |
| An API-management product | No rate limiting, quotas, API keys, transformations or analytics. |
| A guarantee that every app works under a prefix | Rewriting is heuristic for JavaScript. Some apps need their own configuration (section 13). |

### Facts at a glance

| | |
|---|---|
| Language | Go 1.22 or newer, no third-party modules |
| Delivery | Docker image (alpine), built from `Dockerfile` |
| Ports | Container `8443` (HTTPS by default), published as host `443` in `docker-compose.yml` |
| State | `/data/apps.json` (app list) and `/data/tls/` (self-signed certificate) on a Docker volume |
| Memory | About 8 MiB idle; 114 MiB peak while rewriting 24 parallel 10 MB JavaScript files (measured) |
| HTTP versions | HTTP/2 and HTTP/1.1 from clients when TLS is on (HTTP/2 verified); HTTP/1.1 to backends |
| Browsers tested | Chrome only |

---

## 2. Quick start

Requirements: Docker with Compose. The gateway machine must be able to reach the backends (the container reaches LAN addresses such as `172.16.x.x` through Docker's NAT; this was verified).

```bash
cd gateway
cp .env.example .env
# edit .env:
#   ADMIN_TOKEN=<long random string>      e.g. output of:  openssl rand -hex 24
#   TLS_HOSTS=<gateway IP or host name>   goes into the self-signed certificate
docker compose up -d --build
docker compose logs -f
```

Expected log line: `gateway listening https on :8443 (admin: /_admin/)`.

Register your first app (browser: open `https://<gateway>/_admin/`, paste the token, fill the form; or API):

```bash
curl -k -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"name":"app1","target":"http://172.16.16.38:442"}' \
  https://<gateway>/_admin/api/apps
```

Open `https://<gateway>/app1/`. The browser warns about the self-signed certificate until you supply a real one (section 5.1).

Port 443 already in use on the host? Change `"443:8443"` in `docker-compose.yml`, for example to `"8443:8443"`.

Local test without TLS (no certificate warnings, plain HTTP):

```bash
docker run --rm -p 8080:8443 -e TLS=false -e ADMIN_TOKEN=dev -v gw-test:/data gateway-gateway
```

(`gateway-gateway` is the image name Compose gives the build; build it first with `docker compose build`.)

---

## 3. Concepts

| Term | Meaning |
|---|---|
| **App** | One registered entry: a `name` (the URL prefix) plus a `target` and some options. Served at `/<name>/`. |
| **Target** | The real address the app listens on, for example `http://172.16.16.38:442`. May carry a base path (`http://host/base`). |
| **Prefix** | `/<name>`. Removed from the path before forwarding. |
| **Mount** | An extra backend published below an app, at `/<name><path>/`. Used for the API server a frontend calls directly. |
| **Rewrite flags** | `rewrite_html` and `rewrite_js`. Let the gateway edit HTML, CSS and JavaScript bodies so that absolute paths still work under the prefix. |
| **Shim** | A small script the gateway injects into HTML pages (with `rewrite_js`). It patches browser APIs such as `fetch` so paths built at run time also get the prefix. |
| **Storage isolation** | Per-app namespace for `localStorage` and `sessionStorage` (`isolate_storage`). |
| **Token sync** | Coordination of token refresh for a mount whose backend rotates a short-lived token pair in cookies (`sync_tokens`). |
| **Admin token** | The single shared secret that protects the admin API (`ADMIN_TOKEN`). |

---

## 4. How it is supposed to be used

### 4.1 The intended workflow

1. An **operator** runs the gateway once, on a machine that can reach all backends, and sets the admin token.
2. For each web app the operator **registers a name and a target** (admin page or API). If the app works as is, that is all.
3. If the app breaks under a prefix, the operator **turns on the rewrite options one at a time** (section 4.2), guided by the browser console and network tab.
4. **End users** only know `https://<gateway>/<name>/`. They bookmark it or follow the links on the landing page (`https://<gateway>/`).
5. When an app moves or is retired, the operator updates or deletes its entry. Nothing restarts.

Treat the gateway as infrastructure for **trusted internal networks**. It does not authenticate the people using the apps.

### 4.2 Choosing the settings for an app

| Kind of app | Settings | Why |
|---|---|---|
| Plain backend or API; or an app that already builds prefix-aware links (reads `X-Forwarded-Prefix`, uses relative links) | None. Register `name` and `target` only. | Most robust. Nothing is rewritten. |
| Website with root-relative links such as `<a href="/page">` and `<link href="/css/site.css">` | `rewrite_html` | Prefixes those attributes, handles `<base href="/">`, `url(/img.png)` in CSS. |
| Single-page app (Angular, React, Vue) with root-relative asset or API strings in its JavaScript | `rewrite_html` and `rewrite_js` | Prefixes string paths in bundles, injects the shim for paths built at run time. |
| SPA whose API is on another host or port and is hard-coded in the bundle (for example `apiUrl: 'http://host:8039'`) | Add a **mount** | Makes the API same-origin and removes mixed-content errors. |
| App shares storage key names with another app on the gateway (for example both store `gdUserData`) | Add `isolate_storage` (needs `rewrite_js`) | Each app gets its own `localStorage`/`sessionStorage` namespace. |
| Backend uses short-lived access tokens in cookies and rotates the refresh token, and the app logs users out when pages make parallel calls | Add `sync_tokens` | Section 8.9. |
| Backend uses a self-signed certificate | `insecure: true` | Skips certificate verification for that target. |
| React Router or Vue Router app using the browser's real path without a configured base | App-level change needed | The gateway cannot fix router configuration (section 13). Angular works because the gateway sets `<base href="/<name>/">`. |

Rule of thumb: start with nothing, add `rewrite_html`, then `rewrite_js`, then mounts. Each step changes behaviour, so test after each.

### 4.3 Onboarding recipe for a new app

The commands below find the three things that matter: the `<base>` tag, hard-coded API addresses, and how the session is carried. They run from any machine that can reach the app.

```bash
# 1. Entry page: look for <base>, script and link tags
curl -sk https://app.example:8443/ -o index.html
grep -i -E '<base|<script|<link' index.html

# 2. Bundles: look for a hard-coded API address
for f in $(grep -o -E 'src="[^"]+\.js"' index.html | sed 's/src="//;s/"//'); do
  curl -sk "https://app.example:8443/$f" | grep -o -E '(apiUrl|baseUrl|API_URL|webSocket)[^,;]{0,80}'
done

# 3. Any absolute http/https/ws/wss address inside the bundles
curl -sk https://app.example:8443/main.js | grep -o -E '(https?|wss?)://[A-Za-z0-9.:_-]+' | sort -u
```

Then:

1. Register the app with no flags and open it. If it works, stop.
2. If assets 404 or look unstyled, add `rewrite_html`.
3. If API calls go to the wrong path, or the console shows paths without the prefix, add `rewrite_js`.
4. If the bundle hard-codes another host or port for its API, add a mount for it and also turn on `rewrite_js` (the address replacement happens while rewriting the bundle).
5. In the browser's Network tab, look for requests that skip the prefix (404 from the gateway), `401` after the first few seconds, and mixed-content errors.
6. After any change to rewrite behaviour, hard-reload once (the gateway makes rewritten files revalidate, but your browser may hold an older copy).

### 4.4 Two worked examples (the apps this project was tested with)

**A. Angular dev-server app with its API on another port**

```json
{
  "name": "app1",
  "target": "http://172.16.15.169:4201",
  "rewrite_html": true,
  "rewrite_js": true,
  "mounts": [ { "path": "/_api", "target": "http://172.16.15.169:8039" } ]
}
```

The bundle sets `apiUrl: 'http://172.16.15.169:8039'`. After rewriting, the served bundle contains `apiUrl: '/app1/_api'`, so the browser talks to the gateway only.

**B. Angular production build behind nginx, cookie sessions and short-lived tokens**

```json
{
  "name": "collab",
  "target": "https://collaboration.augtrans.com",
  "rewrite_html": true,
  "rewrite_js": true,
  "isolate_storage": true,
  "sync_tokens": "accessToken,refreshToken",
  "mounts": [ { "path": "/_api", "target": "https://collaboration.augtrans.com/api" } ]
}
```

`isolate_storage` is there because both example apps store a login object under the key `gdUserData`. `sync_tokens` is there because this backend issues 5-second access tokens (section 8.9).

### 4.5 For end users

- Use `https://<gateway>/<app name>/`. Bookmark it.
- A certificate warning means the gateway still uses its self-signed certificate; ask the operator for the trusted address or certificate.
- `https://<gateway>/` lists the registered apps.

---

## 5. Configuration reference

### 5.1 Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_TOKEN` | none | Secret for the admin API. `docker-compose.yml` refuses to start without it. If the program itself starts without one, it generates a random token and prints it once in the log. |
| `LISTEN` | `:8443` | Listen address inside the container. |
| `DATA_DIR` | `./data` (resolves to `/data` in the image) | Where `apps.json` and `tls/` live. |
| `TLS` | `true` | `true`: serve HTTPS. Any other value: plain HTTP (use behind a TLS-terminating proxy, or for local tests). |
| `TLS_HOSTS` | empty (compose default: `172.16.15.111`) | Comma-separated IPs and names added to the self-signed certificate. |
| `CERT_FILE`, `KEY_FILE` | empty | Paths to your own PEM certificate and key. Both must be set, otherwise the self-signed certificate is used. Mount them into the container (see the commented lines in `docker-compose.yml`). |
| `GW_DEBUG_AUTH` | `0` | `1` turns on the auth and upload debug log (section 8.14). Keep it off in normal operation. |

Self-signed certificate details: ECDSA P-256, valid 5 years, subject alternative names `localhost`, `127.0.0.1` and everything in `TLS_HOSTS`. It is created once in `/data/tls/cert.pem` and `key.pem` and reused. To change the names, delete those two files and restart. Certificates are read at start; there is no hot reload.

### 5.2 App fields (JSON)

| Field | Type | Default | Meaning and validation |
|---|---|---|---|
| `name` | string | required | URL prefix. Trimmed and lower-cased by the API. Must match `^[a-z0-9][a-z0-9_-]{0,31}$`. `_admin` is reserved. |
| `target` | string | required | `http://` or `https://`, host, optional port, optional base path. A base path is prepended to every forwarded path (`http://h/base` and request `/x` give `/base/x`). |
| `insecure` | bool | `false` | Skip TLS verification when connecting to this target and its mounts. |
| `rewrite_html` | bool | `false` | Rewrite links in HTML and CSS bodies (section 8.4). |
| `rewrite_js` | bool | `false` | Rewrite string paths in JavaScript and inline scripts, and inject the shim into HTML pages (sections 8.5 and 8.6). |
| `isolate_storage` | bool | `false` | Per-app `localStorage` and `sessionStorage` namespace. Takes effect only together with `rewrite_js`; this dependency is not validated, so without `rewrite_js` the flag silently does nothing. |
| `sync_tokens` | string | empty | `"accessCookieName,refreshCookieName"`: two different cookie names (`[A-Za-z0-9_-]`, up to 64 characters). Applies to the app's mounts. |
| `mounts` | array | none | Extra backends under `/<name><path>/` (below). |

Unknown fields in the JSON are **ignored silently**, so a typo such as `rewritejs` does nothing. Check the response, which echoes the stored app.

### 5.3 Mount fields

| Field | Meaning and validation |
|---|---|
| `path` | Must match `^/[a-z0-9_-]{1,32}$` and be unique within the app. Public URL becomes `/<name><path>/`. |
| `target` | Same rules as an app target. A base path is allowed (`https://host/api`). |

A mount proxy applies header, cookie and redirect handling, but **never rewrites bodies**, even when the app has rewrite flags set. It inherits `insecure` and `sync_tokens`.

### 5.4 The persisted list: `apps.json`

`/data/apps.json` is a JSON array of app objects, written atomically (temp file, then rename) and sorted by name:

```json
[
  {
    "name": "app1",
    "target": "http://172.16.15.169:4201",
    "insecure": false,
    "rewrite_html": true,
    "rewrite_js": true,
    "isolate_storage": false,
    "mounts": [ { "path": "/_api", "target": "http://172.16.15.169:8039" } ]
  }
]
```

- Missing file: the gateway starts with an empty list.
- You can **pre-seed** the file before the first start (configuration as code). Verified: a container started with a seeded volume served the seeded app immediately, using a Docker service name as target.
- Invalid JSON or an invalid entry stops the gateway at start with a message such as `parse data/apps.json: ...`. With `restart: unless-stopped` that becomes a restart loop until the file is fixed.
- If the file is read-only (for example a Kubernetes ConfigMap mount), API writes fail with `400` and are rolled back. Choose one management mode: files only, or API.

### 5.5 Files and volumes

| Path in container | Purpose |
|---|---|
| `/gateway` | The program |
| `/data/apps.json` | App list |
| `/data/tls/cert.pem`, `key.pem` | Generated certificate |

The image runs as user `10001`. If you replace the named volume with a host directory, make it writable for that user. The named volume created by Compose in this project is `gateway_gateway-data`.

---

## 6. Admin interface and API

### 6.1 Admin page

`https://<gateway>/_admin/` (the page itself loads without a token; every action needs one).

- Paste the admin token and press **Load**. The table lists apps, targets, mounts and flags.
- The form adds or updates an app (same name = update). Fields: name, target, skip backend TLS verify, rewrite HTML links, rewrite JS, isolate storage, token-sync cookies (`accessToken,refreshToken`), extra mounts (`/_api=http://host:8039, /files=http://host:9000`).
- **Delete** asks for confirmation.
- The token is kept in the tab's `sessionStorage` only.

Status of the page: its script parses (checked on the source file and on the copy served by the running container). The click-through has not been exercised in a browser. The API underneath it is verified.

### 6.2 Admin API

All calls need `Authorization: Bearer <ADMIN_TOKEN>`. Request bodies are limited to 64 KB. There are no CORS headers: call the API from servers and scripts, not from pages on other origins.

| Method and path | Body | Success | Failure |
|---|---|---|---|
| `GET /_admin/api/apps` | none | `200` JSON array of apps, sorted by name | `401` `{"error":"unauthorized"}` |
| `POST /_admin/api/apps` | one app object | `200` the stored app (normalised) | `400` `{"error":"..."}` for bad JSON, validation errors or a failed save; `401` |
| `DELETE /_admin/api/apps/{name}` | none | `204` | `404` `{"error":"not found"}`; `500`; `401` |

`POST` is an **upsert**: the same `name` replaces the existing entry. The change is live immediately; requests already in flight finish on the previous settings.

```bash
# list
curl -sk -H "Authorization: Bearer $T" https://gw/_admin/api/apps

# create or update
curl -sk -X POST -H "Authorization: Bearer $T" -d @app.json https://gw/_admin/api/apps

# delete
curl -sk -X DELETE -H "Authorization: Bearer $T" https://gw/_admin/api/apps/app1
```

### 6.3 Responses the gateway itself produces

| Situation | Response |
|---|---|
| `GET /` | `200` landing page listing app names (public) |
| `/_admin` | `301` to `/_admin/` |
| Unknown app name, unknown admin path | `404 page not found` |
| `/<name>` without trailing slash | `301` to `/<name>/` (query string kept) |
| Admin call without or with a wrong token | `401` |
| Connection refused, name not resolved, TLS or protocol error | `502 bad gateway: <name> unreachable` (verified: a closed port answered `502` within about 2 s) |
| Any timeout: no connection within 10 s (address that drops packets, such as a firewall), TLS handshake over 10 s, or no response headers within 120 s | `504 gateway timeout: <name> did not respond in time` (verified: a non-routable address answered `504` after 10 s) |

---

## 7. How a request flows through the gateway

```
client ──https──> gateway
                   │
                   ├─ /_admin/...      admin page and API
                   ├─ /                landing page
                   └─ /<name>/rest
                         │   unknown name -> 404; no trailing slash -> 301
                         ├─ rest begins with a mount path ? ──> mount proxy
                         └─ otherwise ───────────────────────> app proxy

 proxy, before sending
   • strip /<name> (inside a mount also a duplicated /<name>)
   • prepend the target's base path
   • Host = target host; set X-Forwarded-For/Host/Proto and X-Forwarded-Prefix
   • rewrite Origin and Referer (when they point at the gateway)
   • token sync: hold back / refresh cookies (mounts with sync_tokens)
   • for HTML/JS/CSS paths: ask for an uncompressed body, drop If-None-Match / If-Modified-Since

backend

 proxy, after receiving
   • token sync: learn new cookie values, release waiting requests
   • rewrite Location and Set-Cookie
   • rewrite the body (HTML, CSS, JS) when the flags and the checks below allow it
   • otherwise stream the response through untouched

client
```

Body rewriting happens only when **all** of these hold:

1. The content type is HTML, CSS or JavaScript (`text/html`, `text/css`, `text/javascript`, `application/javascript`, `application/x-javascript`, `application/ecmascript`) **and** the matching flag is on (HTML: either flag; CSS: `rewrite_html`; JavaScript: `rewrite_js`).
2. The request is not `HEAD`, the response is not `101` or `206`.
3. The response is not a download (`Content-Disposition: attachment`).
4. The body is at most 20 MiB. Larger bodies stream through untouched.
5. `Content-Encoding` is absent or `gzip` (gzip is decompressed first). `br` and `deflate` bodies pass through unchanged.

Everything else (images, JSON, PDFs, fonts, archives, source maps) is never altered.

---

## 8. Feature reference

### 8.1 Routing

The first path segment selects the app. Matching is exact and case-sensitive on the lower-case name. After the prefix, the rest of the path and the query string are forwarded unchanged. Mount paths are checked before the app target.

### 8.2 Request headers sent to the backend

| Header | What the backend receives |
|---|---|
| `Host` | The target's host and port, not the gateway's. |
| `X-Forwarded-Prefix` | `/<name>` (for a mount: `/<name><path>`). Apps can use it to build prefix-aware links. |
| `X-Forwarded-Host` | The `Host` the gateway received. |
| `X-Forwarded-Proto` | `https` if the connection into the gateway used TLS, otherwise `http`. |
| `X-Forwarded-For` | The address of the gateway's **direct peer** (a front proxy's address if there is one). |
| `Origin`, `Referer` | Rewritten to the backend's own origin, only when their host equals the `Host` the gateway received. Other origins are left alone. This keeps CSRF and WebSocket origin checks passing. |
| Everything else (`Authorization`, `Cookie`, custom headers such as `X-Real-IP`) | Unchanged, except as listed in sections 8.9 and 8.10. |

Client-supplied `X-Forwarded-*` headers are **replaced, not passed through** (verified: sending spoofed values straight to the gateway produced the gateway's own values at the backend). Use a custom header if the real client address must survive a front proxy (section 9.2).

### 8.3 Response headers and cookies

- **`Location`**: `/new` becomes `/<name>/new`; an absolute URL on the target's host becomes a path under the prefix; protocol-relative `//host/...` and URLs on other hosts are left alone. The target's own base path is removed first (a redirect to `/api/next` through a mount whose target ends in `/api` becomes `/<name>/_api/next`).
- **`Set-Cookie`**: `Domain` is removed. `Path=/` becomes `Path=/<name>/`; a cookie without `Path` gets `Path=/<name>/`; a path inside the target's base path loses that base (`Path=/api/users` through a mount at `/_api` becomes `/<name>/_api/users`). Other attributes (`Secure`, `HttpOnly`, `SameSite`, `Max-Age`) are untouched. Two apps that use the same cookie name therefore do not overwrite each other (verified).
- A cookie set by a mount is scoped to the mount path (`/<name>/_api/`), so it is not sent with page requests to `/<name>/`.

### 8.4 HTML and CSS rewriting (`rewrite_html`)

Applies to **quoted** attributes `href`, `src`, `action`, `poster`, `data-src`, `formaction`, and to CSS `url(...)`.

| Before | After (prefix `/app1`) |
|---|---|
| `<a href="/login">` | `<a href="/app1/login">` |
| `<base href="/">` | `<base href="/app1/">` |
| `<img src="../../../assets/logo.png">` | `<img src="/app1/assets/logo.png">` |
| `background: url(/img/bg.png)` | `background: url(/app1/img/bg.png)` |
| `<a href="//cdn.example/x">`, `<a href="https://other/x">`, `<a href="page.html">` | unchanged |

Why `../` is converted: with `<base href="/">`, extra `../` segments are clamped at the root, so they mean "the root". Under a prefix the browser would still clamp at the gateway root and escape the prefix.

### 8.5 JavaScript rewriting (`rewrite_js`)

Applies to JavaScript files and to inline `<script>` blocks (including `importmap`). It prefixes **string literals that look like root-relative URL paths**:

| Before | After |
|---|---|
| `"/api/users"`, `'/static/a.png'`, `` `/items/${id}` ``, `import("/chunk.js")` | the same strings with `/app1` in front |
| `__webpack_require__.p = "/"` | `__webpack_require__.p = "/app1/"` |
| `"../../assets/x.png"` (static-asset extensions only) | `"/app1/assets/x.png"` |
| `"/"`, `"text/html"`, `"/app1/already"` | unchanged |
| `.replace(/"/g, ...)` (a quote inside a regular expression) | unchanged |
| `navigate(['/login'])`, `navigateByUrl`, `createUrlTree`, `routerLink`, `redirectTo`, `router.url == '/x'` | unchanged (client routers already apply the `<base>` prefix; prefixing them breaks routing) |
| `../../node_modules/x.js` (webpack module ids) | unchanged |

Rules in detail: the whole literal must be URL-shaped (starts with `/` and a letter, `_` or `@`, then URL characters; commas, brackets and `$` are only allowed inside backtick strings); a literal whose opening quote follows `/` or `\` is skipped. Because this is pattern matching, it can be wrong for unusual code (section 13). After changing these rules, syntax-check every served JavaScript file (section 15.3), which caught two real bugs in this project.

JSON files are never rewritten. An app that reads its API address from a runtime `config.json` needs a mount plus its own configuration; the address inside the JSON will not be replaced.

### 8.6 The browser shim (`rewrite_js`)

Injected as the first script of every full HTML document (not into HTML fragments returned by AJAX calls). It adds the prefix, at run time, to root-relative and same-host absolute URLs passed to:

- `fetch`, `XMLHttpRequest.open`, `navigator.sendBeacon`, `window.open`
- `WebSocket` (a relative path becomes an absolute `ws(s)://` URL), `EventSource`, `Worker`, `SharedWorker`
- the setters `src`/`href`/`action` of images, scripts, links, anchors, iframes, sources, media, forms, embeds, and `setAttribute` for the same attributes
- elements inserted later by script (a `MutationObserver`; best effort, because the browser may already have started loading)

It cannot intercept `location.href = "/x"` or `<meta http-equiv="refresh">` when the path is built at run time. Literal paths in the page's own scripts are already fixed by section 8.5.

### 8.7 Mounts

For frontends that call an API server directly at a hard-coded address.

- Public URL: `/<name><path>/...`. The prefix is removed and the target (with its base path) is prepended.
- With `rewrite_js` (or `rewrite_html` for HTML and CSS) every absolute occurrence of the mount target in the app's bodies (`http`, `https`, `ws`, `wss` forms) is replaced by `/<name><path>`.
- Because the frontend and the API are then same-origin, there is no CORS preflight, no mixed content and only one certificate.
- **Duplicate-prefix guard:** JavaScript rewriting may prefix an API path literal that the app later appends to its API base (`apiUrl + "/state/x"` becomes `/app1/_api/app1/state/x`). A mount removes a repeated `/<name>` right after the mount path.

### 8.8 Storage isolation (`isolate_storage`)

All apps share one browser origin, so two apps can overwrite each other's `localStorage` keys. With the option on (and `rewrite_js`), the shim replaces `localStorage` and `sessionStorage` with a proxy that stores keys as `/<name>::key`. Methods, property access, `Object.keys` and `length` only see the app's own keys.

- Existing data stored under the plain keys becomes invisible to the app (the app starts with empty storage, which usually means one fresh login).
- `IndexedDB`, `Cache Storage` and service workers are not isolated. Cookies are already separated by path.

### 8.9 Token sync (`sync_tokens`)

**The problem it solves.** Some backends issue very short-lived access tokens and rotate the refresh token on first use, returning the new pair in `Set-Cookie`. When a page fires several requests at once after the access token has expired, the first one refreshes and gets `200`; the others were already sent with the old cookies and get `401`; many apps log the user out on any `401`. Observed with one real backend: the access token lived 5 seconds; six parallel calls gave `200 401 401 401 401 401`; the same six one after another gave six `200`.

**What the gateway does** (mounts only). For requests carrying the two named cookies it keeps a small in-memory session record keyed by the refresh-cookie value:

1. It reads the access cookie's `exp` claim (no signature check). If the token is still valid, the request goes straight through, with the newest known cookie values.
2. If it is expired, this request becomes the one that refreshes. Other requests for the same session wait.
3. When the response arrives, the gateway learns the new cookie values from `Set-Cookie`, releases the waiting requests, and sends them with the new values.

Only the two named cookies are touched, and their values are never logged. Result in the same test: three bursts of 6, 12 and 12 parallel requests after the token had expired, all `200`; the Ticket page that used to log the user out now loads.

**Requirements and limits**

- The backend must put both tokens in cookies and return new values in `Set-Cookie` when it refreshes. Apps that carry tokens only in an `Authorization` header are not helped.
- If the access cookie is not a JWT, every request for that session is serialised (safe but slower).
- State is in memory per gateway process (capped at 4096 sessions, then reset). With several gateway replicas, use sticky sessions.
- It cannot help when the backend allows only one active session per account and a second login elsewhere ends the first (observed once with the same account; section 14).

### 8.10 Caching and validators

Rewritten responses depend on gateway rules, so they must not be cached as the origin intended. For every body the gateway actually rewrites:

- `ETag` becomes `"gw-<hash of the rewritten body>"`; `Last-Modified`, `Expires` and `Content-Encoding` are removed; `Content-Length` is recalculated; `Cache-Control` becomes `no-cache` (revalidate every time).
- If the browser's `If-None-Match` equals that ETag, the gateway answers `304` itself with no body.
- On the way to the backend, `If-None-Match` and `If-Modified-Since` are dropped for HTML, JS, CSS and extension-less paths, so the backend always returns the full body to be rewritten.
- Rewritten HTML loses its `Content-Security-Policy` header (the rewritten links and the shim would violate it). A CSP in a `<meta>` tag is kept; the shim runs before it is parsed.
- Why it matters: one tested origin marked its hashed bundles `public, immutable` for 30 days; without this rule Chrome kept a broken rewritten file for a month.

Images, fonts and other non-rewritten responses keep the origin's own caching headers.

### 8.11 Uploads, downloads and ranges

Request and response bodies are streamed; the gateway imposes no size limit. Verified through the gateway: a 30 MB multipart upload arrived complete, a 30 MB download was byte-identical, a `Range` request returned `206` with the correct slice, and attachments of type HTML, JS and CSS were not modified. A 25 MB upload also passed through nginx and the gateway to a backend (section 9.2). A real application's upload of six files in one multipart request was forwarded byte for byte.

### 8.12 WebSockets

`Upgrade` requests pass through with the prefix stripped and `Origin` rewritten. Verified directly through the gateway (handshake, server-pushed message, round trip) and to a real backend through a mount. Restarting the gateway drops open sockets.

### 8.13 Timeouts and limits

| Setting | Value |
|---|---|
| Dial and TLS handshake to a backend | 10 s each (then `504`) |
| Wait for backend response headers | 120 s (then `504`) |
| Idle connections kept per backend | up to 32, closed after 90 s |
| Incoming request headers | 15 s |
| Idle client connection | 120 s |
| Total request or response time | not limited (long downloads and sockets are not cut off) |
| Body size | not limited (rewriting stops at 20 MiB per body) |
| Admin request body | 64 KB |

### 8.14 Debug mode (`GW_DEBUG_AUTH=1`)

Writes `AUTHDBG` lines to the log. It exists to diagnose logins, cookies and uploads without exposing secrets:

| Line | Contains |
|---|---|
| per proxied response | app, method, backend path (no query), status, whether `Authorization` and `X-Refresh-Token` were sent, the **names** of cookies sent, `Set-Cookie` names with `max-age`, `path`, `samesite` and flags, names of `X-*` response headers |
| for `5xx` responses | the first 600 bytes of the body |
| for multipart uploads up to 12 MB | field names, file names, content types and sizes; small text values (up to 120 bytes) are logged unless the field name contains `pass`, `token`, `secret`, `auth` or `key` |

Cookie and header **values** are never logged, but small form values **are** (they can be personal data such as an e-mail address), and the body is held in memory while it is read. Turn it off after use: `docker compose up -d` without the variable.

Without debug mode there is **no access log**. The log contains startup lines, `proxy <app> -> <target>: <error>` for backend failures, and Go's own TLS handshake messages (for example when a client sends plain HTTP to the HTTPS port).

---

## 9. Integrating with another solution

This section covers three directions: putting the gateway into another environment, **registering and querying** apps from other systems, and making other applications work well behind it.

Status markers used below: **verified** (run in this project), **follows from the code** (not run), **standard practice** (generic technique, not run here).

### 9.1 The contract

What another solution can rely on:

| Aspect | Guarantee |
|---|---|
| URL layout | App at `/<name>/...`; mount at `/<name><path>/...`; the gateway reserves `/` (landing page) and `/_admin`. |
| What the backend sees | The path with `/<name>` removed (plus the target's base path), `Host` set to the target, `X-Forwarded-Prefix`, `X-Forwarded-Host`, `X-Forwarded-Proto`, `X-Forwarded-For` (section 8.2). All other request headers, methods and bodies unchanged. |
| What the browser sees | Responses unchanged, except `Location`, `Set-Cookie`, and rewritten HTML/CSS/JS bodies with their cache headers (sections 8.3 to 8.10). |
| Configuration | The admin API (section 6.2) or a pre-seeded `apps.json`; changes are live at once. |
| Not provided | Authentication for proxied apps, load balancing, rate limiting, access logs, metrics (section 13). |

### 9.2 Where to put the gateway

**A. Standalone.** Clients reach the gateway directly on port 443 with its self-signed or your own certificate. This is how the project was developed and tested.

**B. Behind another reverse proxy, load balancer or WAF (TLS terminated in front).** Run the gateway with `TLS=false` on an internal port. Verified with nginx in front of a `TLS=false` gateway container, with an upload of 25 MB and rewriting active:

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }

server {
    listen 443 ssl;                       # certificate directives omitted
    client_max_body_size 0;               # the gateway does not limit uploads; do not cap them here
    proxy_request_buffering off;          # stream uploads through

    location / {
        proxy_pass http://gateway-host:8443;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;               # exactly what the browser sent, including the port
        proxy_set_header X-Real-IP $remote_addr;        # real client address (see below)
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;         # WebSockets
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 300s;                        # longer than the gateway's 120 s header wait
    }

    # optional: keep the admin interface off the wider network
    location /_admin/ {
        allow 10.0.0.0/8;  deny all;
        proxy_pass http://gateway-host:8443;
        proxy_set_header Host $http_host;
    }
}
```

The `location /` block, with `listen 80` and plain HTTP, is exactly what was tested. The `listen 443 ssl` line and the `/_admin/` allow-list block are illustrative.

Things to get right:

- **Forward the browser's `Host` unchanged (`$http_host`).** The gateway rewrites `Origin` and `Referer` only when their host equals the `Host` it received. In a test with `Host $host:$server_port` (which sent the container's internal port) the rewrite silently did not happen; with `$http_host` the backend saw its own origin. Verified.
- **The real client address.** The gateway replaces any incoming `X-Forwarded-For`, `X-Forwarded-Host` and `X-Forwarded-Proto` with its own values (the front proxy's address, the `Host` it received, and `http` if the hop into the gateway is plain). Verified: the backend saw `X-Forwarded-For` equal to the nginx container's address. A custom header such as `X-Real-IP` passes through untouched (verified), so use that to carry the client address to backends.
- **`X-Forwarded-Proto` will say `http`** at the backend when the hop proxy-to-gateway is plain HTTP. If a backend builds absolute URLs from it, either send another header it can use, or run TLS between the proxy and the gateway (then the gateway sees TLS and reports `https`; follows from the code).
- **WebSockets through the front proxy** need the `Upgrade` headers shown. The gateway's own handling is verified; the nginx WebSocket leg was not exercised.
- Make the front proxy's read timeout longer than 120 s if backends can be slow.

**C. Same Docker network as the apps (service names as targets).** Verified: a gateway container and a backend container on a user-defined network, target `http://gwint-echo:9000` resolved by name. The apps need no published ports.

```yaml
services:
  gateway:
    build: ./gateway
    restart: unless-stopped
    ports: ["443:8443"]
    environment:
      ADMIN_TOKEN: ${ADMIN_TOKEN:?set it in .env}
      TLS_HOSTS: gateway.example.com
    volumes: [gateway-data:/data]
    networks: [apps]
  crm:
    image: your/crm
    networks: [apps]          # no ports published; register target http://crm:8080
networks:
  apps: {}
volumes:
  gateway-data: {}
```

**D. Backends on the LAN or on the Docker host (this project's setup).** Verified: from inside the container, `172.16.x.x` addresses are reachable through Docker's NAT, and services on the Docker host are reachable as `host.docker.internal` on Docker Desktop. On Linux, use the host's LAN address or add `extra_hosts: ["host.docker.internal:host-gateway"]`.

**E. Kubernetes** (standard practice, not run here). One replica, a PersistentVolumeClaim at `/data`, a Service, an Ingress that terminates TLS and forwards to the pod with `TLS=false`, the admin token in a Secret. Seed `apps.json` through an init step if you want configuration as code, and do not mount it read-only if you also use the API (section 5.4).

### 9.3 Registering and changing apps from automation

The admin API is a plain HTTP/JSON interface and is **idempotent** (`POST` of the same name replaces the entry), which suits provisioning scripts, CI/CD pipelines and service-discovery hooks.

Verified with `curl` (and with Node `fetch`):

```bash
GW=https://gateway.example.com        # add -k to curl while the certificate is self-signed
for f in apps.d/*.json; do
  curl -sS --fail-with-body -X POST -H "Authorization: Bearer $GW_TOKEN" --data-binary @"$f" "$GW/_admin/api/apps" >/dev/null \
    || echo "failed: $f"
done
```

`--fail-with-body` (curl 7.76 or newer) makes curl exit non-zero on a `4xx` and still print the gateway's reason, for example `{"error":"target must be http(s)://host[:port][/path]"}`. With plain `-f` the reason is hidden. Verified: an invalid file was reported and the valid one was applied.

Illustrative (not run here):

```python
import requests
r = requests.post(f"{GW}/_admin/api/apps",
                  headers={"Authorization": f"Bearer {TOKEN}"},
                  json={"name": "reports", "target": "http://10.0.0.7:8080", "rewrite_html": True},
                  verify="/path/to/ca.pem")        # or verify=False while self-signed
r.raise_for_status()
```

```powershell
# PowerShell 7
$body = @{ name = 'reports'; target = 'http://10.0.0.7:8080'; rewrite_html = $true } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri "$GW/_admin/api/apps" -Headers @{ Authorization = "Bearer $env:GW_TOKEN" } `
  -Body $body -ContentType 'application/json' -SkipCertificateCheck
```

Patterns:

| Need | How |
|---|---|
| Desired-state sync | Keep one JSON file per app in version control, `POST` each, optionally `DELETE` names that are no longer listed (`GET` returns the current set). |
| Drift detection | `GET /_admin/api/apps` and compare with your files. Compare the fields you manage; the gateway adds defaults (`insecure`, `rewrite_html`, `rewrite_js`, `isolate_storage`). |
| Bootstrapping a fresh gateway | Pre-seed `apps.json` into the data volume before first start (verified), or run the loop above right after start. |
| Safe rollout of a rewrite change | `POST` the updated entry (live at once); keep the previous JSON to roll back. |
| Fail-fast validation | A `400` response carries the reason (`name must match ...`, `target must be http(s)://...`, `mount path must be unique ...`, `sync_tokens must be two different cookie names ...`). |

Remember that unknown JSON fields are ignored; check the echoed response for the fields you meant to set.

### 9.4 Querying and monitoring the gateway from another system

| What you want | How |
|---|---|
| Is the process up? | `GET /` returns `200` (public). Verified. |
| Is the configuration loaded? | `GET /_admin/api/apps` with the token returns `200` and the list. Verified. |
| Is a particular app reachable? | `GET /<name>/`: `502` connection refused or similar, `504` timeout (packets dropped, or no answer within 120 s), `404` not registered; anything else is the backend's own answer. |
| What is registered? | `GET /_admin/api/apps`. |
| Container health check (verified inside the image) | see below |
| Logs | stdout and stderr: `docker logs`, or your logging driver. Startup, backend errors and TLS handshake messages only. |
| Deep diagnosis of logins, cookies and uploads | `GW_DEBUG_AUTH=1` (section 8.14), temporarily. |
| Metrics | None built in. Use an external probe (blackbox-style checks on `/` and `/<name>/`) or add middleware in `main.go`. |

Compose health check (works with the self-signed certificate because BusyBox `wget` is told not to verify it; exit code 1 on failure):

```yaml
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "--no-check-certificate", "https://127.0.0.1:8443/"]
      interval: 30s
      timeout: 5s
      retries: 3
```

With `TLS=false` use `http://127.0.0.1:8443/` and drop the flag.

### 9.5 Authentication and single sign-on

The gateway does **not** know who the users of the proxied apps are. Options:

- **Keep the apps' own logins.** They work through the gateway (verified with two real apps). Cookies are path-scoped per app, and `isolate_storage` separates browser storage.
- **Add a gate in front** (standard practice, not run here): an authenticating reverse proxy (basic auth, mTLS, an OIDC proxy such as oauth2-proxy), or a VPN or IP allow-list. Place it in front of the gateway as in topology B, and always protect `/_admin/`.
- **SSO callbacks.** An identity provider redirects the browser to a registered redirect URI. Register the **gateway's** URL (`https://<gateway>/<name>/...`). The gateway rewrites a redirect only when it targets the backend's own host; a redirect to any other host (for example an identity provider or an internal name) passes through unchanged (verified), which is usually what you want for the provider but not for a redirect back to the app under its internal name.
- **One session per account.** Some backends end an existing session when the same account logs in elsewhere (observed once, section 14). Two people, or you and an automated test, sharing one account will log each other out.

### 9.6 Making your own application gateway-friendly

If an app follows these rules it needs **no rewrite flags at all**, which is the most robust setup:

1. Use relative URLs (`page.html`, `./api/x`), or build absolute paths from the `X-Forwarded-Prefix` header or a configurable base path.
2. Make the API base configurable and relative (`/api` resolved against the page, or an environment value), never a hard-coded host and port.
3. Do not hard-code your own host name in links, redirects or `Location` headers.
4. Set cookies without a `Domain`, and with a relative or no `Path`.
5. For single-page apps, configure the router's base path (`<base href>`, `basename`, `PUBLIC_URL`) from the deployment, not from code.
6. Do not rely on `location.pathname` being `/`.
7. Avoid inline absolute URLs in runtime `config.json` (JSON is not rewritten).
8. Keep access tokens long enough to survive a page that makes several calls at once, or make refresh tolerant of near-simultaneous requests.
9. Make forms and uploads send each field once (a `multipart` request that repeats a field as both text and file can confuse some servers; see section 14).

### 9.7 Reusing the code in another Go solution

The program is a `main` package, not a library, but the pieces are small and separable:

| Piece | File | Use |
|---|---|---|
| Prefix-stripping reverse proxy with header, cookie and redirect fixes | `proxy.go` (`newProxyAt`) | Copy into your own service or call it as a handler. |
| Body rewriting (HTML, CSS, JS, mounts, cache rules) | `rewrite.go` (`rewriteBody`) | Standalone logic; input is an `*http.Response` plus the app settings. |
| Browser shim | `shim.js` | Embedded with `go:embed`; usable in any HTML-injecting layer. |
| Token coordination | `tokensync.go` | Self-contained. |
| App store with validation and atomic persistence | `store.go` | Replace with your own source of truth (database, service registry) by feeding `Store.Put`. |

Typical extension points: add a rewrite rule in `rewrite.go`; add a request or response header rule in the `Rewrite` and `ModifyResponse` hooks of `newProxyAt`; add an authentication check or access log in `Server.ServeHTTP` (`main.go`); trust an upstream `X-Forwarded-For` by copying it from `pr.In.Header` inside `Rewrite`. There is no license file in the repository.

### 9.8 State, backup and high availability

| Topic | Facts |
|---|---|
| What to back up | The data volume: `apps.json` (names, targets and options; no secrets unless you put credentials in a target, which you should not) and optionally `tls/`. |
| Backup and restore (standard Docker usage) | `docker run --rm -v gateway_gateway-data:/data alpine cat /data/apps.json` to read; copy the file back into the volume to restore; or export with `GET` and re-import with the `POST` loop in 9.3. |
| Several replicas | Possible for stateless traffic, but each replica has its own `apps.json` (keep them identical through your automation) and its own in-memory token sync (use sticky sessions if you use `sync_tokens`). There is no clustering. |
| Restart behaviour | A restart takes a second or two, drops open WebSockets and in-flight requests, and keeps the app list. There is no graceful drain. Browser sessions survive (they live in cookies and storage). |

---

## 10. Security model and hardening checklist

**Model.** The admin token is the only protection of the admin API. Everything reachable through a registered target is reachable by anyone who can reach the gateway. The gateway is built for trusted internal networks.

Checklist:

- [ ] Use a long random admin token (`openssl rand -hex 24`). Keep `.env` out of version control and shared chats. (This directory is not under version control; add `.env` to `.gitignore` if you change that.) Rotate by editing `.env` and running `docker compose up -d`.
- [ ] Restrict `/_admin/` by network (allow-list at the front proxy, VPN, firewall). Do not expose it to the internet.
- [ ] Anyone holding the admin token can make the gateway connect to **any host and port it can reach** (internal services, cloud metadata addresses) and read the answer through `/<name>/`. Treat the token like network-level access and consider an egress firewall for the gateway.
- [ ] Use a trusted certificate (`CERT_FILE`/`KEY_FILE`) or terminate TLS in front; the self-signed one trains users to click through warnings.
- [ ] With `TLS=false` the admin token travels in clear text on that hop. Keep that hop on a trusted network.
- [ ] Put authentication in front if the apps have none (section 9.5).
- [ ] Remember that **all apps share one browser origin**. A script in one app can read another app's `localStorage` (unless `isolate_storage` is on) and make requests to the others. Do not host untrusted apps next to trusted ones.
- [ ] Rewritten HTML loses its `Content-Security-Policy` header, which weakens that app's protection.
- [ ] Do not put credentials in targets (`http://user:pass@host`).
- [ ] Keep `GW_DEBUG_AUTH` off (it logs small form values).
- [ ] The landing page lists app names to anyone; hide it at the front proxy if names are sensitive.
- [ ] The container runs as an unprivileged user (`10001`).

---

## 11. Operations runbook

| Task | Command or action |
|---|---|
| Start or update after changing the code | `docker compose up -d --build` (app list persists; brief interruption) |
| Stop | `docker compose down` (the volume is kept; `down -v` deletes it) |
| Follow the log | `docker compose logs -f` |
| See running state | `docker compose ps` |
| Change the admin token | edit `.env`, then `docker compose up -d` |
| Change certificate names | delete `/data/tls/cert.pem` and `key.pem` from the volume, restart (or use `CERT_FILE` and `KEY_FILE`) |
| Turn debug on for a session | `GW_DEBUG_AUTH=1 docker compose up -d`; off again with a plain `docker compose up -d` |
| Fix a broken `apps.json` | correct or remove the file in the volume, then restart |
| Roll back a change to one app | `POST` its previous JSON |
| Capacity | One small container. Observed 8 MiB idle; rewriting dominates CPU and memory because each rewritten body is held in memory (up to 20 MiB) while it is processed. |

Windows note: with Git Bash, Docker commands that contain Linux paths (`-v ...:/app/x`) are altered by path conversion. Set `MSYS_NO_PATHCONV=1` for them.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `404 page not found` at `/<name>/` | App not registered, or the name has capitals (names are lower-case and case-sensitive) | Check `GET /_admin/api/apps`. |
| `502 ... unreachable` | Backend down, wrong host or port, firewall, or `http` versus `https` mismatch. The log line `proxy <app> -> <target>: ...` gives the reason (`connection refused`, `i/o timeout`, `no such host`, `tls: first record does not look like a TLS handshake`, `server gave HTTP response to HTTPS client`) | Test from inside the container: `docker compose exec gateway wget -q -O - <target>`. Fix the target or scheme. |
| `502` with `x509: certificate signed by unknown authority` | Backend uses a self-signed or private-CA certificate | Set `insecure: true`. |
| `504 ... did not respond in time` | The gateway could not connect within 10 s (firewall dropping packets, wrong network), or the backend took longer than 120 s to start answering | Check routing and firewalls from the gateway host; for slow backends fix the backend or raise `ResponseHeaderTimeout` in `proxy.go`. |
| Page loads without styles or images; 404 for `/assets/...` at the gateway root | Root-relative or `<base href="/">` links | Turn on `rewrite_html`. |
| API calls go to the wrong path; requests miss the prefix | Paths in JavaScript | Turn on `rewrite_js`. |
| Browser blocks calls as mixed content, or login does nothing | Frontend calls a hard-coded `http://host:port` API from an HTTPS page | Add a mount for that API and use `rewrite_js`. |
| `Cannot match any routes ... 'app1/login'` (Angular) | Router paths were prefixed | Already excluded by the rewrite rules; check you are on the current build and hard-reload once. |
| Blank page and `SyntaxError` in a bundle | A rewrite rule damaged the script | Syntax-check the served files (section 15.3); report the file and the matched text. |
| Fix does not seem to take effect | Browser still holds an older copy (a long-lived `immutable` cache from before the fix) | Hard-reload once; rewritten files now revalidate every time. |
| Logged out right after login, or on pages that make several calls | Short-lived tokens with rotation and parallel requests | Add `sync_tokens`. |
| Logged out when someone else logs in with the same account | Backend allows one active session per account (appears so) | Use separate accounts. |
| App cannot find its login after adding another app | Two apps use the same storage key | `isolate_storage` (with `rewrite_js`). |
| Cookie not sent back | Path scoping: a cookie set by a mount only goes to the mount path | Keep the cookie-consuming calls under the same mount, or make the app set it on the page path. |
| Redirect leaves the gateway | The backend redirects to another host name (SSO, internal name) | Register the gateway URL as redirect URI or fix the backend's configured base URL. |
| `Origin` or CSRF checks fail behind a front proxy | The front proxy changed `Host` | Forward `Host $http_host` unchanged (section 9.2). |
| Browser certificate warning | Self-signed certificate, or you used an address not in `TLS_HOSTS` | Use a real certificate or add the name to `TLS_HOSTS`. |
| Container exits at start with `parse data/apps.json: ...` | Broken `apps.json` | Fix or delete it. |
| Downloaded HTML, JS or CSS file differs from the original | Should not happen: attachments are never rewritten | Check the response really has `Content-Disposition: attachment`. |
| Admin page shows nothing after pasting the token | Wrong token, or an old image with a broken page script | Check the API with `curl`; rebuild (`docker compose up -d --build`). |

---

## 13. Limitations and things that are not implemented

**Limits of the rewriting approach**

- JavaScript rewriting is pattern matching. A literal such as `pathname.startsWith("/admin")` or `API_BASE + "/x"` where `API_BASE` is a full backend URL can be changed wrongly. Turn `rewrite_js` off for such an app.
- Client-side routers that read `location.pathname` (React Router without `basename`, Vue Router in history mode without `base`) need their own base-path setting.
- A path built at run time and assigned to `location.href` cannot be intercepted (confirmed: it landed on the gateway root and returned 404). Literal paths in scripts are fixed.
- An `innerHTML` insertion with a path built at run time sends one unprefixed request before the shim corrects it (confirmed; cosmetic).
- Unquoted HTML attributes (`href=/x`) are not rewritten.
- JSON bodies and runtime `config.json` files are never rewritten.
- Brotli and deflate bodies, and bodies over 20 MiB, are passed through without rewriting.
- Cookies set via a mount are scoped to the mount path.
- All apps share one browser origin; `IndexedDB` and `Cache Storage` are not isolated.

**Not implemented**

- Multiple targets per app, health checks, failover.
- Access log, metrics endpoint, health endpoint, audit log of admin changes.
- Trusting an upstream `X-Forwarded-For` (it is replaced).
- Per-app authentication.
- Graceful shutdown (a restart drops in-flight requests and sockets).
- Hot reload of certificates.
- Automated tests in the repository (verification was done with ad-hoc scripts; section 14).

**Not verified in this project**

- A click-through of the admin page in a browser (its script parses; the API is verified).
- WebSocket traffic through an upstream proxy such as nginx.
- Kubernetes deployment.
- Browsers other than Chrome.
- Embedding gateway pages in iframes (backend `X-Frame-Options` headers pass through untouched on non-rewritten responses).

---

## 14. Verification record and field notes

The full evidence log is in [GATEWAY-NOTES.md](GATEWAY-NOTES.md). Summary of what was exercised:

| Area | Result |
|---|---|
| Prefix routing, redirects, 404, runtime add/change/delete, persistence | Verified |
| Header, cookie and `Location` rewriting; `Origin`/`Referer` rewriting | Verified |
| HTML, CSS and JavaScript rewriting, shim, router-path exclusions | Verified on two real Angular apps and on throwaway backends |
| Mounts, duplicate-prefix guard, cookie and redirect base-path handling | Verified |
| WebSockets (direct) | Verified |
| Uploads (30 MB), downloads (30 MB, byte-identical), ranges, attachments untouched | Verified |
| In-app upload and PDF view on the first real app | Verified (stored file byte-identical) |
| Timeouts (`504` at 120 s), backend down (`502`), huge admin body, corrupted `apps.json` | Verified |
| Storage isolation, cache revalidation (`no-cache`, `304`) | Verified |
| Token sync | Verified: three bursts (6, 12 and 12 parallel requests) after token expiry, all `200`; the page that previously logged the user out loads |
| Integration through nginx, service-name targets, seeded `apps.json`, spoofed forwarding headers, HTTP/2, container health check | Verified (section 9) |
| Memory | 8 MiB idle; 114 MiB peak under 24 parallel 10 MB rewrites |

### Field notes from the two real applications

**App A (`app1`, Angular dev server plus API on another port).** All 122 sidebar pages loaded with data and no failed requests. The only console errors are form and sanitizer errors that exist in the app's own code (a diff of original and served bundles shows the rewrite does not touch that code).

**App B (`collab`, Angular production build behind nginx).** All 24 menu pages loaded. The access token lives 5 seconds, the refresh token 3 days; the app never refreshes tokens client-side and logs out on any `401`; the backend rotates tokens through cookies and `x-access-token`/`x-refresh-token` headers. Through the gateway, six parallel calls after expiry gave one `200` and five `401`; one after another they all succeeded; token sync removes the parallel-request case. On the original site (no gateway) a page opened shortly after login also ended at the login screen, but the same account was signed in in another tab at the time, so that observation cannot be separated from the one-session-per-account effect below. It does not prove the logout is independent of the gateway.

Observed behaviours worth knowing:

- Only one active session per account appears to be allowed: after a login on the original site, the gateway session's next request returned `401` with no refresh attempt.
- Customer registration (`POST /api/customer/register`) answered `500 Internal Server Error` through the gateway. With debug mode on, the captured multipart request showed the browser sending the file fields twice: once as plain text (`profile_picture=C:\fakepath\...`, `installation_certificate=C:\fakepath\...`) and once as real file parts. The gateway forwarded exactly the same 3,644 bytes it received. The same registration has **not yet been compared against the original site**, so the cause (the app's payload versus the backend) is unconfirmed.
- Image files referenced by customer and user records return `404` from the original site as well.
- Two bugs found in the gateway while testing this app and fixed: a JavaScript regular expression `/"/g,"` being taken for a string, and a 30-day `immutable` cache hiding the fix.

---

## 15. Source map and development

### 15.1 Files

| File | Role |
|---|---|
| `main.go` | Start-up, listener, routing, admin API, landing page |
| `store.go` | App and mount model, validation, atomic persistence |
| `proxy.go` | Reverse proxy per app and per mount: path, headers, cookies, redirects, errors |
| `rewrite.go` | Body rewriting (HTML, CSS, JavaScript, mounts), cache and download rules |
| `shim.js` | Browser shim, embedded into the binary |
| `tokensync.go` | Token coordination for mounts |
| `debug.go` | Opt-in auth and upload debug log |
| `tlsgen.go` | Self-signed certificate generation |
| `ui.html` | Admin page (embedded) |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | Packaging and configuration |
| `GATEWAY-NOTES.md` | Detailed verification log |

### 15.2 Build and run without Docker

Needs Go 1.22 or newer (the Docker build uses 1.23). Not run on this machine, which has no Go toolchain; the Docker build runs the same `go build`.

```bash
cd gateway
go build -o gateway .
ADMIN_TOKEN=dev TLS=false LISTEN=:8080 DATA_DIR=./data ./gateway
```

### 15.3 Regression check after changing rewrite rules

Every JavaScript file the gateway serves must still parse. This check found two real bugs.

```bash
# for each bundle URL of an app, compare the original with the gateway's copy
node --check original.mjs      # must pass (if it fails, the original is not valid JS either)
node --check via-gateway.mjs   # must pass
```

Name the files `.mjs` so module syntax is accepted. The admin page script can be checked the same way: extract the text between `<script>` and `</script>` in `ui.html` and run `node --check` on it.

### 15.4 Useful test recipes (all used in this project)

- **Burst test for token sync** (in the browser console of an app page, after waiting longer than the access-token lifetime): fire six `fetch` calls to different API endpoints at once with `credentials: 'include'` and print the status codes; all should be `200`.
- **Upload and download integrity**: upload a known file, fetch it back through the gateway, compare SHA-256; repeat with a `Range` header and expect `206`.
- **Timeout test**: a backend that waits 130 s before answering must give `504` at about 120 s.
- **Header test**: a small echo backend that returns the headers it received shows exactly what the gateway forwards.
- **Rewrite test**: `curl` the page through the gateway and check `<base href>` and a few `href`/`src` values; `curl` a JavaScript file and compare string literals with the original.

### 15.5 Appendix: minimal `docker-compose.yml` in this project

```yaml
services:
  gateway:
    build: .
    restart: unless-stopped
    ports:
      - "443:8443"
    environment:
      ADMIN_TOKEN: ${ADMIN_TOKEN:?set ADMIN_TOKEN in .env}
      TLS: "true"
      GW_DEBUG_AUTH: ${GW_DEBUG_AUTH:-0}
      TLS_HOSTS: ${TLS_HOSTS:-172.16.15.111}
    volumes:
      - gateway-data:/data

volumes:
  gateway-data:
```
