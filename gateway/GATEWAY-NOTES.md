# Gateway: verified behaviour, optimizations and conditions

A Go reverse proxy that exposes many web apps (different VMs and ports) behind one IP and port, each under its own path:

```
https://<gateway>/app1/   ->  http://172.16.15.169:4201   (frontend)
https://<gateway>/app1/_api/ -> http://172.16.15.169:8039 (its API, "mount")
```

Apps are added and removed at runtime from the admin UI (`/_admin/`) or the admin API. Nothing needs a restart.

**How to read the status column**

- **Verified**: tested end to end, either against a dummy backend or against the real app (Angular 16 dev server `172.16.15.169:4201` plus API `:8039`).
- **Implemented**: in the code, but not exercised by a test or a real run.

---

## 1. Routing and app management

| # | Behaviour | Status |
|---|---|---|
| 1.1 | Path-prefix routing: first path segment selects the app (`/app1/...`). The prefix is stripped before forwarding, so the app sees `/foo` for `/app1/foo`. Everything after the prefix (including the query string) is passed through unchanged. | Verified |
| 1.2 | `/app1` (no trailing slash) redirects with `301` to `/app1/`, keeping the query string. | Verified |
| 1.3 | Unknown app returns `404`. `/` shows a landing page listing the registered apps. | Verified |
| 1.4 | Apps are added, updated and deleted at runtime. The route table is swapped under an `RWMutex`, so there is no restart and no dropped traffic. | Verified |
| 1.5 | The list is persisted in `/data/apps.json` on a Docker volume and survives restarts and rebuilds. | Verified |
| 1.6 | Atomic save (write temp file, then rename). If saving fails, the in-memory change is rolled back. | Implemented |
| 1.7 | Target path is honoured: `http://host:port/base` joins `base` with the request path. | Implemented |
| 1.8 | Input validation: name must match `[a-z0-9][a-z0-9_-]{0,31}`; `_admin` is reserved; target must be `http(s)://host[:port]`; mount paths must be unique and match `/[a-z0-9_-]+`. Bad input returns `400` with a message. | Verified |

## 2. Admin security

| # | Behaviour | Status |
|---|---|---|
| 2.1 | Admin API requires `Authorization: Bearer <ADMIN_TOKEN>`. Constant-time comparison. Missing or wrong token returns `401`. | Verified |
| 2.2 | `ADMIN_TOKEN` is required by `docker-compose.yml`. If the app starts without one, it generates a random token and logs it once. | Implemented |
| 2.3 | Request body limit of 64 KB on admin writes. | Implemented |
| 2.4 | Admin UI builds the table with `textContent` (no HTML injection from app names or targets). | Implemented |
| 2.5 | Container runs as non-root user `10001`. | Implemented |

## 3. TLS and listener

| # | Behaviour | Status |
|---|---|---|
| 3.1 | Serves HTTPS on container port `8443`, mapped to host `443`. | Verified |
| 3.2 | Self-signed ECDSA P-256 cert generated on first start, with SANs for `localhost`, `127.0.0.1` and every entry of `TLS_HOSTS`. Stored in the data volume and reused. | Verified |
| 3.3 | Own cert supported with `CERT_FILE` / `KEY_FILE`. `TLS=false` gives plain HTTP for local tests. | Implemented / Verified (`TLS=false`) |
| 3.4 | `ReadHeaderTimeout` 15 s, `IdleTimeout` 120 s. No write timeout, so long downloads and WebSockets are not cut off. | Implemented |

## 4. Proxy transport (per app)

| # | Behaviour | Status |
|---|---|---|
| 4.1 | Dial timeout 10 s, TLS handshake timeout 10 s, response-header timeout 120 s, idle connection pool of 32 per host, keep-alive 30 s. | Implemented |
| 4.2 | `insecure: true` skips certificate checks to that backend only (self-signed HTTPS backends). | Implemented |
| 4.3 | Environment proxy settings are ignored, so backends on the LAN are reached directly. | Implemented |
| 4.4 | Backend down or unreachable returns `502 bad gateway: <app> unreachable`. The reason is logged. | Verified |
| 4.5 | Request headers: `Host` becomes the target host; `X-Forwarded-For`, `-Host`, `-Proto` are set; `X-Forwarded-Prefix` carries the app prefix. | Verified |
| 4.6 | `Origin` and `Referer` are rewritten to the **app's frontend origin** (its `target`), also for mount (API) traffic, but only when they point at the gateway's host. Cross-site values are left alone. This keeps CORS allow-lists, CSRF and WebSocket origin checks passing. Mounts used to get their own origin, which broke the Injectoplast login: its API (`:8051`) allows only its frontend (`:4205`) in CORS and answered `500 Not allowed by CORS`. Unit-tested (`TestMountOriginIsTheAppFrontend`); Gepdec and Smart Meter answer identically before and after. | Verified (real app) |

## 5. Response header rewriting

| # | Behaviour | Status |
|---|---|---|
| 5.1 | `Location`: `/new` becomes `/app1/new`; absolute URLs on the backend host become relative paths under the prefix; protocol-relative `//host` is left alone. | Verified |
| 5.2 | `Set-Cookie`: `Path=/` becomes `Path=/app1/`, a missing `Path` gets `/app1/`, and `Domain=` is removed. Apps no longer overwrite each other's cookies. | Verified |

## 6. Body rewriting (`rewrite_html` / `rewrite_js`)

Applied only to `text/html`, `text/css` and JavaScript content types. Everything else (images, JSON, PDFs, binaries) is streamed unchanged.

### 6.1 HTML and CSS (`rewrite_html`)

| # | Behaviour | Status |
|---|---|---|
| 6.1.1 | `href`, `src`, `action`, `poster`, `data-src`, `formaction` starting with `/` get the prefix. | Verified |
| 6.1.2 | Bare root attributes are handled too, in particular `<base href="/">` becomes `<base href="/app1/">`. Without this, relative links like `assets/css/x.css` resolved to the gateway root and returned 404. | Verified (real app) |
| 6.1.3 | Excess `../` is clamped to the app root. At a root deployment `../../../assets/x.png` resolves to `/assets/x.png`; under a prefix it would escape the prefix. It is rewritten to `/app1/assets/x.png`. | Verified (real app logo) |
| 6.1.4 | CSS `url(/img.png)` is prefixed, in stylesheets, in HTML `<style>` blocks and `style=""` attributes, and in component styles that frameworks embed as strings in JavaScript (Angular `styles: ['... url(/assets/bg.png) ...']`). Lowercase `url(` only, not part of a longer name, so JS such as `new URL(...)` is untouched; already-prefixed paths are left alone. Found with the Injectoplast login wallpaper, which requested `/assets/...` from the outer site. Unit-tested in `rewrite_test.go`; all scripts of both test apps pass `node --check` after the change. | Verified (real app) |

### 6.2 JavaScript (`rewrite_js`)

| # | Behaviour | Status |
|---|---|---|
| 6.2.1 | Root-path string literals in JS files and inline `<script>` blocks are prefixed (`"/api/x"`, `'/static/a.png'`, `` `/items/${id}` ``, `import("/chunk.js")`). | Verified |
| 6.2.2 | Left alone: `"/"`, MIME strings like `"text/html"`, already-prefixed paths (no `/app1/app1`), JSON script blocks, non-JS script types. | Verified |
| 6.2.3 | webpack `__webpack_require__.p = "/"` becomes `"/app1/"`. | Verified |
| 6.2.4 | `importmap` blocks are rewritten. | Implemented |
| 6.2.5 | Client-router paths are skipped: `navigate([...])`, `navigateByUrl`, `createUrlTree`, `parseUrl`, `routerLink`, `redirectTo`, `router.url == '...'`. Angular already strips the `<base>` prefix, so prefixing these produced `NG04002: Cannot match any routes 'app1/login'`. | Verified (real app) |
| 6.2.6 | `../` asset literals (`../../x.png`, fonts, css, media) become `/app1/x`, restricted to static-asset extensions. webpack module ids such as `../node_modules/x.js` are never touched. | Verified (real app) |
| 6.2.7 | Absolute backend URLs are replaced with the mount path (see section 7). | Verified |

### 6.3 Safe-handling conditions for any rewritten response

| # | Condition | Status |
|---|---|---|
| 6.3.1 | `Accept-Encoding` is removed on rewritable requests so the body comes back plain. If a backend gzips anyway, the gateway decompresses it. `br` / `deflate` bodies pass through unmodified. | Verified (gzip) |
| 6.3.2 | Bodies over 20 MB pass through unrewritten (streamed, not buffered). | Implemented |
| 6.3.3 | `HEAD` requests, `101` (WebSocket) and `206` (partial) responses are never rewritten. | Verified (`206`) |
| 6.3.4 | **Downloads are never rewritten**: any response with `Content-Disposition: attachment` is byte-for-byte untouched. Found with a test: downloaded `.html` / `.js` / `.css` files were being corrupted. | Verified |
| 6.3.5 | Rewritten HTML drops `Content-Security-Policy` (the rewritten links and shim would violate it). | Verified |
| 6.3.6 | Rewritten bodies get a new `ETag` (`"gw-<hash>"`) and lose `Last-Modified`, and `Content-Length` is recalculated. | Verified |
| 6.3.7 | **Stale-cache fix**: for HTML, JS, CSS and extensionless paths the gateway strips `If-None-Match` and `If-Modified-Since` on the way to the backend. Otherwise the backend answered `304`, and Chrome kept an old rewritten file after the rules changed (this caused the lingering `app1/login` router error). Images, fonts and other assets keep normal caching. | Verified (real app) |

## 7. API mounts (`mounts`)

For frontends that call their API at a hardcoded absolute URL. The real app had `apiUrl: 'http://172.16.15.169:8039'` and `webSocket: 'ws://172.16.15.169:8039'`, so Chrome blocked every call from the HTTPS page as mixed content, and login could not work.

| # | Behaviour | Status |
|---|---|---|
| 7.1 | `mounts: [{path: "/_api", target: "http://172.16.15.169:8039"}]` exposes the API at `/app1/_api/...`. Mount traffic gets header and cookie rewriting but no body rewriting. | Verified |
| 7.2 | Absolute URLs of the mount target in HTML, JS and CSS (`http`, `https`, `ws`, `wss` forms) are rewritten to `/app1/_api`. Verified: `apiUrl: '/app1/_api'`. | Verified |
| 7.3 | Because the frontend and API are then same-origin, no CORS preflight, no mixed content, one TLS cert. | Verified (login and dashboard work) |
| 7.4 | **Duplicate-prefix guard**: the app builds `apiUrl + "/state/api/..."` where the literal was already prefixed by the JS rewrite, giving `/app1/_api/app1/state/...` (404 "Error fetching state data"). The mount proxy strips a repeated `/app1` right after the mount path. | Verified (real app) |
| 7.5 | WebSocket to the mount path connects and stays open, the same as connecting directly. | Verified |

## 8. WebSocket

| # | Behaviour | Status |
|---|---|---|
| 8.1 | `Upgrade` requests pass through with prefix stripping, a `101` handshake and bidirectional frames (server push plus echo round trip). | Verified |
| 8.2 | The `Origin` header is rewritten to the backend origin on the upgrade request. | Verified (header rewrite) |
| 8.3 | The runtime shim converts a relative `WebSocket("/path")` to an absolute `ws(s)://host/path` URL. | Implemented |
| 8.4 | A gateway restart drops open sockets (the app logged one `CloseEvent`). The app reconnects on reload. | Known |

## 9. Runtime shim (injected with `rewrite_js`)

A small script inserted right after `<head>` of full HTML documents only (not AJAX fragments). It prefixes root-relative and same-host absolute URLs for things that static rewriting cannot see.

| # | Patched API | Status |
|---|---|---|
| 9.1 | `fetch`, `XMLHttpRequest.open`, `navigator.sendBeacon`, `window.open` | Verified (fake DOM) |
| 9.2 | `WebSocket`, `EventSource`, `Worker`, `SharedWorker` constructors | Verified (fake DOM) |
| 9.3 | `.src`, `.href`, `.action` property setters; `setAttribute` for `src`, `href`, `action`, `poster`, `formaction`, `data-src` | Verified (fake DOM) |
| 9.4 | `MutationObserver` fixing elements inserted later (`innerHTML`). Best effort: the browser may already have started the request. | Implemented |
| 9.5 | Excess `../` on element URLs resolves to the app root. | Implemented |
| 9.6 | Already-prefixed URLs and other-origin URLs are left alone. | Verified (fake DOM) |

## 10. Uploads and downloads

Tested through the gateway against a throwaway backend, so no data in the real app was changed.

| # | Behaviour | Status |
|---|---|---|
| 10.1 | 30 MB `multipart/form-data` upload arrives complete (31,457,560 bytes) in about 0.8 s. Streamed, not buffered; there is no body-size cap in the gateway. | Verified |
| 10.2 | 30 MB binary download is identical (SHA-256 match), in about 1.7 s. `Content-Disposition` and `Content-Length` preserved. | Verified |
| 10.3 | `Range` requests return `206` with the correct slice (needed for PDF and video viewers and resumable downloads). | Verified |
| 10.4 | Attachments of type HTML, JS and CSS stay unmodified (6.3.4). | Verified |
| 10.5 | The real app's own upload or download flow (it loads files from `/_api/<path>` into iframes and objects) has **not** been exercised, because that needs writing records to the app's database. | Not verified |

## 11. Real-app results (Angular 16 app at `172.16.15.169:4201`, API `:8039`)

| Check | Result |
|---|---|
| Login page via `/app1/` | Redirects to `/app1/login`; logo, background and fonts load; no console errors |
| Login | Works once the mount is in place (credentials entered by the user, not by the gateway tooling) |
| Dashboard | Loads; KPI cards, charts and filters render; API calls return `200` through `/app1/_api/` |
| Deep link and reload on `/app1/dashboard` | Works; session survives reload |
| All 122 sidebar pages | Each loads with data; no failed HTTP requests, no error toasts, no blank pages |
| Console errors seen | Form errors (`reading 'valid'`, `NG01052`) and `NG0904` on Documentation pages. A diff of the original and served bundles shows the rewrite does not touch form or sanitizer code, so these appear to be the app's own bugs. Not yet confirmed by running the app directly. |
| Bug not caused by the gateway | The app builds `getState?allCheck=truecountry_id=[...]` with a missing `&`; the backend still answers `200` |

## 12. Known limits

- **Client-side routing is not rewritten.** Frameworks that read `location.pathname` need the app's own base path configured (`<base href>`, `basename`, `PUBLIC_URL`). Angular works here because the gateway sets `<base href="/app1/">`. React Router and Vue Router apps still need their own setting.
- **JS rewriting is a heuristic.** String literals that look like root paths get the prefix, so code comparing against `location.pathname`, or concatenating a full backend URL onto a path, can break. Turn `rewrite_js` off for that app.
- **`location.href = "/x"` and `<meta http-equiv="refresh">`** cannot be intercepted by the shim.
- **Early requests from `innerHTML`** may fire before the shim fixes them. Use `rewrite_html` as well.
- **Brotli and deflate bodies and responses over 20 MB** are passed through unrewritten.
- **All apps share one origin.** `localStorage` and cookies with the same name can collide, and a script in one app can read another's storage.
- **Admin token is the only protection** on the admin API. The landing page that lists app names is public.
- **A gateway restart** drops open WebSockets and in-flight requests.

## 13. Troubleshooting lessons (symptoms we actually hit)

| Symptom | Cause | Fix in gateway |
|---|---|---|
| Page unstyled, assets 404 at `/assets/...` | `<base href="/">` + relative links | 6.1.2 |
| `NG04002 ... 'app1/login'` | JS rewrite prefixed router paths | 6.2.5 |
| Old behaviour persists after a fix | Browser kept a rewritten file with the backend's ETag | 6.3.6, 6.3.7 |
| Logo 404 | `../../../assets/...` escaping the prefix | 6.1.3, 6.2.6 |
| Login impossible | API hardcoded to `http://host:8039`; mixed content | Section 7 |
| `Error fetching state data` (404 on `/app1/_api/app1/state/...`) | Doubled prefix on API literals | 7.4 |
| Downloaded text/HTML/JS file differs from the original | Body rewritten | 6.3.4 |

## 14. Files

| File | Role |
|---|---|
| `main.go` | Listener, routing, admin API, landing page |
| `store.go` | App and mount model, validation, persistence |
| `proxy.go` | Reverse proxy, header, cookie and origin rewriting |
| `rewrite.go` | Body rewriting (HTML, CSS, JS, mounts), download/cache safety rules |
| `shim.js` | Browser runtime shim (embedded into the binary) |
| `tlsgen.go` | Self-signed certificate generation |
| `ui.html` | Admin page |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | Packaging and configuration |

## 15. Limitation tests (executed)

Every limit from section 12 and the "what can still go wrong" list was tried on purpose. The in-app tests ran against the test project (`172.16.15.169:4201`). The rest ran against a throwaway backend that was removed afterwards.

### 15.1 In-app upload and download (real test project)

| Test | Result |
|---|---|
| Create a Balance Sheet record with a PDF attached (form POST, `multipart/form-data`) | `POST /app1/_api/mycompany/api/v1/addMyComapanyFinancials` returned `200`; the record appears in the table; the attachments dialog lists the file |
| Open the attachment (PDF viewer iframe) | Renders ("GATEWAY UPLOAD TEST"); loaded from `/app1/_api//mycompany/<id>_gateway-upload-test.pdf` |
| Byte check of the stored file fetched through the gateway | 403 bytes, SHA-256 identical to the local file, `Content-Type: application/pdf` |
| `Range: bytes=0-9` on the stored file | `206`, `Content-Range: bytes 0-9/403` |
| Session after a gateway rebuild | Still logged in; dashboard and API calls return `200` |

The record is left in the test project (Gepdec Smarttech LlP, April 2024 - March 2025, description "GATEWAY UPLOAD TEST - safe to delete"). The app has no delete icon on that page.

### 15.2 Limits that were confirmed (they do happen)

| Limit | What was seen | Mitigation |
|---|---|---|
| Shared `localStorage` | App B read the value App A had stored (`ls:FROM_APP_A`) | Give each app its own origin (subdomain) if keys can collide |
| Router reads `location.pathname` | Page saw `/lima/router`, not `/router` | Configure the app's base path |
| Runtime-built `location.href` | `location.href = '/' + 'dest'` went to `https://localhost/dest` and returned the gateway's 404 | Literal paths are fixed by the static rewrite; paths built from variables are not |
| Runtime-built `innerHTML` image | One request went out unprefixed first (`/dynX.png`), then the shim re-requested it with the prefix | Cosmetic: one wasted request; final URL is correct |
| Backend sends Brotli | Body passed through intact (`Content-Encoding: br` kept, decodes correctly) but its links are not rewritten | Backend should not force `br`; the gateway removes `Accept-Encoding` on rewritable requests, so this only happens if the backend ignores it |
| HTML over 20 MB | `200`, full 26,342,454 bytes streamed, links untouched (25,600 original `href="/x"` unchanged) | None needed; it is not buffered |
| Redirect to a host that is not the backend host (SSO/OAuth style) | `Location: http://app.corp.internal:9300/callback?code=1` left as is, so the browser leaves the gateway | Configure the SSO redirect URI to the gateway URL, or add that host to the app's rewrite rules |
| Redirect to the backend's own host | Rewritten to `/lima/landing` | Works as intended |
| Landing page without a token | Lists every app name | Add auth in front of the gateway if names are sensitive |
| Certificate SANs | Only `localhost`, `127.0.0.1` and `172.16.15.111`; any other name or IP shows a browser warning | Set `TLS_HOSTS`, or supply a real certificate |
| Corrupted `apps.json` | Container exits with code 1 and `parse data/apps.json: invalid character...` | Restore or delete the file (it is in the data volume) |
| Mixed content to a hardcoded `http://host:port` API | `blocked: Failed to fetch` without a mount | Add the API as a mount (verified working: `ok:pong-from-api`) |

### 15.3 Limits that did not materialize (better than feared)

| Concern | Result |
|---|---|
| `<meta>` Content-Security-Policy blocks the shim | The shim runs before the meta tag is parsed, so it still works (`shim=true`) |
| `location.href = "/dest"` with a literal path in a page script | Fixed by the static rewrite (landed on `/lima/dest`) |
| `innerHTML` with a literal `src="/x.png"` in a page script | Fixed by the static rewrite before any request |
| Two apps setting the same cookie name | Isolated by the rewritten `Path`: app A got `sid=AAA`, app B got `sid=BBB`, the root got none |
| Admin API accepts huge bodies | Body over 64 KB is rejected (`400`) |
| Memory with many large rewrites | 24 parallel requests for a 10.5 MB JS file: 60 MiB idle, 114 MiB peak, 55 MiB afterwards |

### 15.4 Timeouts (and a fix made during testing)

| Test | Result |
|---|---|
| Backend that answers after 130 s | Gateway gave up at exactly 120 s |
| Before the fix | `502 bad gateway: <app> unreachable`, which was misleading |
| After the fix | `504 gateway timeout: <app> did not respond in time` |
| Backend with nothing listening | Still `502 bad gateway: <app> unreachable` |

## 16. Second real site: `https://collaboration.augtrans.com` (Angular production build behind nginx)

Registered as `collab` with `rewrite_html`, `rewrite_js`, `isolate_storage` and the mount `/_api -> https://collaboration.augtrans.com/api`. Its bundle sets `apiUrl: "https://collaboration.augtrans.com/api"` and calls it with cookies (`withCredentials`), so without the mount it would be a cross-origin credentialed call.

Three problems showed up, all fixed in the gateway:

| Problem | Cause | Fix |
|---|---|---|
| Blank page, console `SyntaxError: Invalid regular expression flags` in `scripts-*.js` | The minified bundle contains `.replace(/"/g,"&quot;")`. The JS rewrite read `"/g,"` (starting at the quote inside the regex) as a string and prefixed it, giving `/"/collab/g`. | Plain-quote literals no longer allow `, ( ) { } [ ] $`; a literal whose opening quote follows `/` or `\` is skipped. All JS files of both sites were then syntax-checked with `node --check`: all valid. |
| Fix did not reach the browser | The origin sends `Cache-Control: public, immutable` with `max-age=2592000` (30 days) on hashed assets, so Chrome kept the broken file and never asked again. | Rewritten responses now get `Cache-Control: no-cache` (and no `Expires`), a `gw-` ETag, and the gateway answers `304` itself when the browser's `If-None-Match` matches. Verified: `200` then `304` with an empty body. Unrewritten assets (images, fonts) keep the origin's long cache. |
| Still blank: `Cannot read properties of undefined (reading 'refreshToken')` | Both apps use the key `gdUserData` in `localStorage`, and the apps share one origin. The Gepdec app's user object (different shape) was read by this app. | New option `isolate_storage`: the shim replaces `localStorage` and `sessionStorage` with a per-app namespaced proxy (`/<app>::key`). Methods, property access, `Object.keys` and `length` only see the app's own keys. After the fix the login page renders and the app sees an empty storage. |

Notes on `isolate_storage`:

- It needs `rewrite_js` (it lives in the shim) and is off by default. Turning it on for an app that already has a login in its old keys logs that app out once.
- It does not isolate `IndexedDB`, `Cache API`, service workers or cookies. Cookies are already separated by the rewritten `Path`.
- Login and the pages behind it on this site have not been exercised yet, because the password has to be entered by a person.

## 17. `collab` menu walk, session behaviour and a cookie fix

All 24 menu pages of `https://collaboration.augtrans.com` were opened through the gateway (dashboard, Master x10, Schedule and maintenance x3, Generation, Customer x3, Tickets, Asset management x3, User management x2).

| Result | Detail |
|---|---|
| All pages load and show data | 1 to 25 rows each. Every API call returns `200` except the items below. |
| App empty-state messages | "No schedule data found", "no data available", "No country data found": the app's own warnings for empty lists. |
| Image `404`s | Customer and user profile images (`/customer/<file>`, `/users/<file>`) return `404` from the origin too, checked directly. The files do not exist there. |
| Console errors on empty schedule pages | `applyScheduleFilter ... reading 'filter'` on empty data. This is app code, unrelated to the gateway. |

### Why a page could log the user out (backend behaviour, reproduced)

| Observation | Value |
|---|---|
| Access token lifetime (`exp - iat`, read from the token without printing it) | 5 seconds |
| Refresh token lifetime | 3 days |
| The app | Sends `Authorization` and `x-refresh-token` on every request, never refreshes client-side, and calls `forceLogout()` on any `401` |
| Backend | Refreshes server-side and returns new tokens in `x-access-token` / `x-refresh-token` response headers (cookies carry the session too) |
| Test: six parallel calls after the access token expired | First `200`, the other five `401` (repeated in three rounds) |
| Test: the same six calls one after another | All `200` |
| Test: one call at a time on the Inventory page | `200`, `200`, table with rows, no console errors |

Reading: the backend appears to rotate the refresh token on first use, so concurrent requests that still carry the old one get `401`, and the app turns that into a logout. A page that fires several calls at once right after the 5-second token expires can trigger it. The gateway answers a `401` exactly as fast as the origin (0.07 s versus 0.05 s) and adds no delay; the behaviour was not compared by logging in on the origin directly. The robust fix is on the backend or app side (longer access-token lifetime, or tolerate the old refresh token briefly).

### Gateway fix made during this check

Cookie `Path` and redirect `Location` rewriting for a mount now remove the mount target's own base path. Example with target `.../api`: `Set-Cookie: a=1; Path=/api/users` becomes `Path=/<app>/_api/users` (it used to become `/<app>/_api/api/users`, which the browser would never send back); `Location: /api/next` becomes `/<app>/_api/next`. Tested with a local backend.

### Browser note

Walking many pages in one tab piles up thousands of console errors (the app logs an error on every empty schedule list) and the tab can stop responding. The gateway stayed idle at 0% CPU while this happened. Use a fresh tab for long walks.
