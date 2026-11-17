package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha1"
	_ "embed"
	"encoding/hex"
	"io"
	"net/http"
	"net/url"
	"path"
	"regexp"
	"strconv"
	"strings"
)

//go:embed shim.js
var shimJS string

const maxRewriteBody = 20 << 20

var (
	htmlAttrRe = regexp.MustCompile(`(?i)(\s(?:href|src|action|poster|data-src|formaction)\s*=\s*["'])/([^/"'])`)
	// "../../x": with <base href="/"> excess ../ clamp to the root, so it means "/x"
	htmlUpRe = regexp.MustCompile(`(?i)(\s(?:href|src|action|poster|data-src|formaction)\s*=\s*["'])(?:\.\./)+([^"'./][^"']*)`)
	// same in JS string literals, only for static asset files (never webpack module ids like ../x.js)
	jsUpRe = func() []*regexp.Regexp {
		var out []*regexp.Regexp
		for _, q := range jsQuotes {
			q = regexp.QuoteMeta(q)
			out = append(out, regexp.MustCompile(`(?i)`+q+`(?:\.\./)+([A-Za-z_@][A-Za-z0-9_\-.~%@:+,=&?#/]*\.(?:png|jpe?g|gif|svg|ico|webp|avif|bmp|css|woff2?|ttf|otf|eot|mp4|webm|mp3|pdf)(?:\?[^"'\x60\s]*)?)`+q))
		}
		return out
	}()
	// bare root: <base href="/">, <a href="/">
	htmlRootRe = regexp.MustCompile(`(?i)(\s(?:href|src|action|poster|data-src|formaction)\s*=\s*["'])/(["'])`)
	// url(/x) as CSS writes it: lowercase and not part of a longer name, so JavaScript such as
	// new URL(...) or obj.url(...) is never touched
	cssURLRe = regexp.MustCompile(`(^|[^\w$.])(url\(\s*\\?["']?)/([^/"'\\)][^"'\\)]*)`)

	scriptBlockRe = regexp.MustCompile(`(?is)(<script\b[^>]*>)(.*?)(</script>)`)
	scriptTypeRe  = regexp.MustCompile(`(?i)\btype\s*=\s*["']?([^"'\s>]+)`)
	headOpenRe    = regexp.MustCompile(`(?i)<head(?:\s[^>]*)?>`)
	htmlOpenRe    = regexp.MustCompile(`(?i)<html(?:\s[^>]*)?>`)
	docMarkerRe   = regexp.MustCompile(`(?i)<(?:!doctype|html|head)\b`)

	// "/path", '/path', `/path` string literals; whole literal must look like a URL path.
	jsQuotes = []string{`"`, `'`, "`"}
	jsLitRe  = func() []*regexp.Regexp {
		var out []*regexp.Regexp
		// plain quotes: URL-ish characters only (no , ( ) { } [ ] $), so "/g," inside a
		// regex literal such as /"/g,"x" is never taken for a path. Backticks may hold ${...}.
		const plain = `[A-Za-z_@][A-Za-z0-9_\-.~%@:+=&?#/]*`
		const tmpl = `[A-Za-z_@][A-Za-z0-9_\-.~%@:+,=&?#/(){}$\[\]]*`
		for _, q := range jsQuotes {
			cls := plain
			if q == "`" {
				cls = tmpl
			}
			q = regexp.QuoteMeta(q)
			out = append(out, regexp.MustCompile(q+`/(`+cls+`)`+q))
		}
		return out
	}()
	// Literals that are client-side router paths (Angular/Vue/React Router already apply the
	// <base> prefix themselves): navigate(['/x']), navigateByUrl('/x'), routerLink, redirectTo, router.url == '/x'
	jsRouterCtxRe = regexp.MustCompile(
		`(?:navigate(?:ByUrl)?|createUrlTree|parseUrl)\s*\(\s*(?:\[[^\]]{0,200})?$` +
			`|redirectTo\s*:\s*$` +
			`|routerLink["']?\s*[,:=]\s*$` +
			`|\.url\s*[!=]==?\s*$` +
			`|\.url\.(?:includes|startsWith|indexOf)\(\s*$`)
	// webpack publicPath: __webpack_require__.p = "/"
	jsPublicPathRe = regexp.MustCompile(`(\.p\s*=\s*)(?:"/"|'/')`)
)

type bodyKind int

const (
	kindNone bodyKind = iota
	kindHTML
	kindCSS
	kindJS
)

func classify(ct string) bodyKind {
	ct = strings.ToLower(ct)
	switch {
	case strings.HasPrefix(ct, "text/html"):
		return kindHTML
	case strings.HasPrefix(ct, "text/css"):
		return kindCSS
	case strings.HasPrefix(ct, "text/javascript"),
		strings.HasPrefix(ct, "application/javascript"),
		strings.HasPrefix(ct, "application/x-javascript"),
		strings.HasPrefix(ct, "application/ecmascript"):
		return kindJS
	}
	return kindNone
}

// rewriteMounts replaces absolute URLs of each mount target (any of http/https/ws/wss)
// with the gateway path of that mount.
func rewriteMounts(b []byte, a App, prefix string) []byte {
	for _, m := range a.Mounts {
		u, err := url.Parse(m.Target)
		if err != nil {
			continue
		}
		base := u.Host + strings.TrimRight(u.Path, "/")
		to := []byte(prefix + m.Path)
		for _, scheme := range []string{"http", "https", "ws", "wss"} {
			b = bytes.ReplaceAll(b, []byte(scheme+"://"+base), to)
		}
	}
	return b
}

// maybeRewritable guesses from the path whether a response may be HTML/JS/CSS.
func maybeRewritable(p string) bool {
	switch strings.ToLower(path.Ext(p)) {
	case "", ".html", ".htm", ".js", ".mjs", ".css":
		return true
	}
	return false
}

func rewriteBody(resp *http.Response, a App, prefix string) error {
	kind := classify(resp.Header.Get("Content-Type"))
	replace := len(a.Replace) > 0
	switch kind {
	case kindHTML:
		if !a.RewriteHTML && !a.RewriteJS && !replace {
			return nil
		}
	case kindCSS:
		if !a.RewriteHTML && !replace {
			return nil
		}
	case kindJS:
		if !a.RewriteJS && !replace {
			return nil
		}
	default:
		return nil
	}
	if resp.ContentLength > maxRewriteBody || resp.StatusCode == http.StatusSwitchingProtocols ||
		resp.StatusCode == http.StatusPartialContent ||
		resp.Request != nil && resp.Request.Method == http.MethodHead {
		return nil
	}
	// file downloads must reach the user byte for byte
	if cd := strings.ToLower(strings.TrimSpace(resp.Header.Get("Content-Disposition"))); strings.HasPrefix(cd, "attachment") {
		return nil
	}

	var src io.Reader = resp.Body
	gz := false
	switch strings.ToLower(resp.Header.Get("Content-Encoding")) {
	case "":
	case "gzip":
		zr, err := gzip.NewReader(resp.Body)
		if err != nil {
			return err
		}
		src, gz = zr, true
	default:
		return nil // br/deflate etc: leave untouched
	}

	b, err := io.ReadAll(io.LimitReader(src, maxRewriteBody+1))
	if err != nil {
		resp.Body.Close()
		return err
	}
	if len(b) > maxRewriteBody {
		if gz {
			resp.Body.Close()
			return io.ErrShortBuffer
		}
		// identity encoding: stream the original bytes through untouched
		resp.Body = struct {
			io.Reader
			io.Closer
		}{io.MultiReader(bytes.NewReader(b), resp.Body), resp.Body}
		return nil
	}
	resp.Body.Close()

	b = rewriteMounts(b, a, prefix)
	switch kind {
	case kindHTML:
		b = rewriteHTMLDoc(b, a, prefix)
		resp.Header.Del("Content-Security-Policy")
	case kindCSS:
		if a.RewriteHTML {
			b = rewriteCSSURLs(b, prefix)
		}
	case kindJS:
		if a.RewriteJS {
			b = rewriteJSMode(b, prefix, a.JSPaths == "files")
		}
	}
	for _, r := range a.Replace { // last, so the replacement text itself is never rewritten again
		b = bytes.ReplaceAll(b, []byte(r.From), []byte(r.To))
	}

	// the body differs from what the backend's validators describe
	sum := sha1.Sum(b)
	etag := `"gw-` + hex.EncodeToString(sum[:8]) + `"`
	resp.Header.Set("ETag", etag)
	resp.Header.Del("Last-Modified")
	resp.Header.Del("Content-Encoding")
	// The origin may mark hashed assets "immutable" for 30 days. A rewritten body depends on
	// gateway rules, so browsers must revalidate it, otherwise a rule fix never reaches them.
	resp.Header.Del("Expires")
	resp.Header.Set("Cache-Control", "no-cache")
	if resp.Request != nil {
		if inm, _ := resp.Request.Context().Value(inmKey{}).(string); inm == etag {
			resp.StatusCode, resp.Status = http.StatusNotModified, "304 Not Modified"
			resp.Body = http.NoBody
			resp.ContentLength = 0
			resp.Header.Del("Content-Length")
			resp.Header.Del("Content-Type")
			return nil
		}
	}
	resp.Body = io.NopCloser(bytes.NewReader(b))
	resp.ContentLength = int64(len(b))
	resp.Header.Set("Content-Length", strconv.Itoa(len(b)))
	return nil
}

// rewriteCSSURLs prefixes root-relative CSS url(/x) references. Used for stylesheets, for <style>
// blocks and style="" attributes in HTML, and for component styles that frameworks (Angular, Vue,
// Svelte) embed as strings inside JavaScript. Paths already under the prefix are left alone.
func rewriteCSSURLs(b []byte, prefix string) []byte {
	name := prefix[1:]
	return cssURLRe.ReplaceAllFunc(b, func(m []byte) []byte {
		sm := cssURLRe.FindSubmatch(m)
		rest := string(sm[3])
		if rest == name || strings.HasPrefix(rest, name+"/") {
			return m
		}
		return []byte(string(sm[1]) + string(sm[2]) + prefix + "/" + rest)
	})
}

func rewriteHTMLDoc(b []byte, a App, prefix string) []byte {
	if a.RewriteHTML {
		b = rewriteCSSURLs(b, prefix)
		b = htmlAttrRe.ReplaceAll(b, []byte("${1}"+prefix+"/${2}"))
		b = htmlRootRe.ReplaceAll(b, []byte("${1}"+prefix+"/${2}"))
		b = htmlUpRe.ReplaceAll(b, []byte("${1}"+prefix+"/${2}"))
	}
	if !a.RewriteJS {
		return b
	}
	// inline <script> bodies (also importmap)
	b = scriptBlockRe.ReplaceAllFunc(b, func(m []byte) []byte {
		sm := scriptBlockRe.FindSubmatch(m)
		if t := scriptTypeRe.FindSubmatch(sm[1]); t != nil {
			ty := strings.ToLower(string(t[1]))
			if !strings.Contains(ty, "javascript") && !strings.Contains(ty, "module") && ty != "importmap" {
				return m
			}
		}
		out := make([]byte, 0, len(m))
		out = append(out, sm[1]...)
		out = append(out, rewriteJSMode(sm[2], prefix, a.JSPaths == "files")...)
		out = append(out, sm[3]...)
		return out
	})
	// runtime shim, only for full documents (not ajax fragments)
	if !docMarkerRe.Match(b) {
		return b
	}
	js := strings.ReplaceAll(shimJS, "__PREFIX__", prefix)
	js = strings.ReplaceAll(js, "__ISOLATE__", strconv.FormatBool(a.IsolateStorage))
	tag := []byte("<script>" + js + "</script>")
	if loc := headOpenRe.FindIndex(b); loc != nil {
		return splice(b, loc[1], tag)
	}
	if loc := htmlOpenRe.FindIndex(b); loc != nil {
		return splice(b, loc[1], tag)
	}
	return append(tag, b...)
}

func splice(b []byte, at int, ins []byte) []byte {
	out := make([]byte, 0, len(b)+len(ins))
	out = append(out, b[:at]...)
	out = append(out, ins...)
	return append(out, b[at:]...)
}

// fileLitRe: a path literal that names a file or a dev-server module (kept when js_paths = "files")
var fileLitRe = regexp.MustCompile(`(?i)^(?:@|node_modules/)|\.(?:m?js|cjs|css|map|json|html?|png|jpe?g|gif|svg|ico|webp|avif|bmp|woff2?|ttf|otf|eot|mp4|webm|mp3|wav|ogg|pdf|txt|xml|wasm)(?:[?#].*)?$`)

// rewriteJS prefixes string literals that look like root-relative URL paths. Heuristic.
func rewriteJS(b []byte, prefix string) []byte {
	return rewriteJSMode(b, prefix, false)
}

// rewriteJSMode: filesOnly limits the prefixing to path literals that name files (see App.JSPaths).
func rewriteJSMode(b []byte, prefix string, filesOnly bool) []byte {
	name := prefix[1:]
	for i, re := range jsUpRe {
		q := jsQuotes[i]
		b = re.ReplaceAllFunc(b, func(m []byte) []byte {
			if bytes.Contains(m, []byte("node_modules")) {
				return m
			}
			rest := m[1 : len(m)-1]
			for bytes.HasPrefix(rest, []byte("../")) {
				rest = rest[3:]
			}
			return []byte(q + prefix + "/" + string(rest) + q)
		})
	}
	for i, re := range jsLitRe {
		q := jsQuotes[i]
		idx := re.FindAllIndex(b, -1)
		if idx == nil {
			continue
		}
		out := make([]byte, 0, len(b)+len(idx)*len(prefix))
		last := 0
		for _, m := range idx {
			path := string(b[m[0]+2 : m[1]-1])
			if path == name || strings.HasPrefix(path, name+"/") || strings.HasPrefix(path, name+"?") {
				continue
			}
			if filesOnly && !fileLitRe.MatchString(path) {
				continue
			}
			// opening quote right after "/" or "\" is part of a regex literal or an escaped
			// string (e.g. /"/g), not the start of a string
			if m[0] > 0 && (b[m[0]-1] == '/' || b[m[0]-1] == '\\') {
				continue
			}
			ctxStart := m[0] - 240
			if ctxStart < 0 {
				ctxStart = 0
			}
			if jsRouterCtxRe.Match(b[ctxStart:m[0]]) {
				continue
			}
			out = append(out, b[last:m[0]]...)
			out = append(out, q...)
			out = append(out, prefix...)
			out = append(out, '/')
			out = append(out, path...)
			out = append(out, q...)
			last = m[1]
		}
		b = append(out, b[last:]...)
	}
	b = rewriteCSSURLs(b, prefix) // component styles embedded in JS: url(/assets/bg.png)
	return jsPublicPathRe.ReplaceAll(b, []byte(`${1}"`+prefix+`/"`))
}
