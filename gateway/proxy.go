package main

import (
	"context"
	"crypto/tls"
	"errors"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"regexp"
	"strings"
	"time"
)

type inmKey struct{}

type proxyHandler struct {
	rp   *httputil.ReverseProxy
	sync *syncNames // token sync (mounts only), nil when off
}

func (p *proxyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if p.sync != nil {
		var c *syncCtx
		r, c = withSync(r, p.sync)
		defer c.release() // also covers error paths
	}
	p.rp.ServeHTTP(w, r)
}

var (
	cookiePath = regexp.MustCompile(`(?i);\s*path=[^;]*`)
	cookieDom  = regexp.MustCompile(`(?i);\s*domain=[^;]*`)
)

func newProxy(a App, target *url.URL) *proxyHandler {
	return newProxyAt(basePath+"/"+a.Name, a, target, target)
}

// newProxyAt proxies requests under prefix (e.g. "/app1" or "/app1/_api") to target. pageOrigin is
// the app's own frontend address: the browser's page lives there when used directly, so Origin and
// Referer are presented as coming from it — also for mounts, whose APIs typically allow only their
// frontend's origin in CORS (e.g. API :8051 accepts Origin :4205, not :8051).
func newProxyAt(prefix string, a App, target, pageOrigin *url.URL) *proxyHandler {
	dupe := ""
	if appPrefix := basePath + "/" + a.Name; prefix != appPrefix {
		dupe = appPrefix
	}
	tr := &http.Transport{
		Proxy:                 nil,
		DialContext:           (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		TLSClientConfig:       &tls.Config{InsecureSkipVerify: a.Insecure},
		MaxIdleConnsPerHost:   32,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: 120 * time.Second,
	}

	rp := &httputil.ReverseProxy{
		Transport: tr,
		Rewrite: func(pr *httputil.ProxyRequest) {
			stripped := strings.TrimPrefix(pr.In.URL.Path, prefix)
			// Mount proxies: the static JS rewrite may have prefixed an API path literal that the
			// app appends to apiUrl, giving /app1/_api/app1/x. Drop the duplicate /app1.
			if dupe != "" && (stripped == dupe || strings.HasPrefix(stripped, dupe+"/")) {
				stripped = strings.TrimPrefix(stripped, dupe)
			}
			if stripped == "" {
				stripped = "/"
			}
			pr.SetURL(target) // sets scheme+host, Out.Host = target host
			pr.Out.URL.Path = strings.TrimRight(target.Path, "/") + stripped
			pr.Out.URL.RawPath = ""
			pr.SetXForwarded()
			pr.Out.Header.Set("X-Forwarded-Prefix", prefix)
			if a.ScriptName && dupe == "" { // the app itself, not its mounts
				pr.Out.Header.Set("X-Script-Name", prefix)
			}
			rewriteOriginHeaders(pr, pageOrigin, basePath+"/"+a.Name)
			if debugAuth {
				peekMultipart(a.Name, pr)
			}
			if c := syncFrom(pr.In.Context()); c != nil {
				c.prepare(pr)
			}
			if (a.RewriteHTML || a.RewriteJS || len(a.Replace) > 0) && maybeRewritable(stripped) {
				// ask for a plain body so it can be rewritten, and never let the
				// backend answer 304 against a validator for a body we rewrote
				pr.Out.Header.Del("Accept-Encoding")
				pr.Out.Header.Del("If-None-Match")
				pr.Out.Header.Del("If-Modified-Since")
				// remember the browser's validator so a rewritten body can be answered with 304
				if inm := pr.In.Header.Get("If-None-Match"); inm != "" {
					pr.Out = pr.Out.WithContext(context.WithValue(pr.Out.Context(), inmKey{}, inm))
				}
			}
		},
		ModifyResponse: func(resp *http.Response) error {
			if debugAuth {
				logAuthShape(a.Name, resp)
				peekErrorBody(a.Name, resp)
			}
			if c := syncFrom(resp.Request.Context()); c != nil {
				c.onResponse(resp)
			}
			base := strings.TrimRight(target.Path, "/")
			rewriteLocation(resp, prefix, base, target)
			rewriteCookies(resp, prefix, base)
			return rewriteBody(resp, a, prefix)
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			log.Printf("proxy %s -> %s: %v", a.Name, target, err)
			var ne net.Error
			if errors.As(err, &ne) && ne.Timeout() {
				http.Error(w, "gateway timeout: "+a.Name+" did not respond in time", http.StatusGatewayTimeout)
				return
			}
			http.Error(w, "bad gateway: "+a.Name+" unreachable", http.StatusBadGateway)
		},
	}
	h := &proxyHandler{rp: rp}
	if dupe != "" && a.SyncTokens != "" {
		h.sync, _ = parseSyncNames(a.SyncTokens)
	}
	return h
}

// stripBase removes the target's own base path (e.g. "/api") from a backend-relative path,
// because the gateway adds that base itself when forwarding.
func stripBase(p, base string) string {
	if base != "" && (p == base || strings.HasPrefix(p, base+"/")) {
		p = strings.TrimPrefix(p, base)
		if p == "" {
			p = "/"
		}
	}
	return p
}

func rewriteLocation(resp *http.Response, prefix, base string, target *url.URL) {
	loc := resp.Header.Get("Location")
	if loc == "" {
		return
	}
	switch {
	case strings.HasPrefix(loc, "//"):
		return
	case loc == prefix || strings.HasPrefix(loc, prefix+"/") || strings.HasPrefix(loc, prefix+"?"):
		return // the app already built it under the prefix (X-Script-Name)
	case strings.HasPrefix(loc, "/"):
		resp.Header.Set("Location", prefix+stripBase(loc, base))
	default:
		u, err := url.Parse(loc)
		if err == nil && u.IsAbs() && u.Host == target.Host {
			p := u.EscapedPath()
			if !strings.HasPrefix(p, "/") {
				p = "/" + p
			}
			rel := prefix + p
			if u.RawQuery != "" {
				rel += "?" + u.RawQuery
			}
			if u.Fragment != "" {
				rel += "#" + u.Fragment
			}
			resp.Header.Set("Location", rel)
		}
	}
}

func rewriteCookies(resp *http.Response, prefix, base string) {
	cs := resp.Header.Values("Set-Cookie")
	if len(cs) == 0 {
		return
	}
	resp.Header.Del("Set-Cookie")
	for _, c := range cs {
		c = cookieDom.ReplaceAllString(c, "")
		if cookiePath.MatchString(c) {
			c = cookiePath.ReplaceAllStringFunc(c, func(m string) string {
				i := strings.Index(strings.ToLower(m), "path=")
				p := strings.TrimSpace(m[i+5:])
				if !strings.HasPrefix(p, "/") {
					p = "/"
				}
				if p == prefix || strings.HasPrefix(p, prefix+"/") {
					return "; Path=" + p // already under the prefix
				}
				return "; Path=" + prefix + stripBase(p, base)
			})
		} else {
			c += "; Path=" + prefix + "/"
		}
		resp.Header.Add("Set-Cookie", c)
	}
}

// rewriteOriginHeaders makes Origin/Referer look like the app's own frontend origin (the page's
// address when used directly), so CORS allow-lists and CSRF / WebSocket origin checks keep passing
// through the gateway. appPrefix is the app's public path, removed from the Referer path.
func rewriteOriginHeaders(pr *httputil.ProxyRequest, origin *url.URL, appPrefix string) {
	self := origin.Scheme + "://" + origin.Host
	prefix := appPrefix
	if o := pr.In.Header.Get("Origin"); o != "" {
		if u, err := url.Parse(o); err == nil && u.Host == pr.In.Host {
			pr.Out.Header.Set("Origin", self)
		}
	}
	if ref := pr.In.Header.Get("Referer"); ref != "" {
		if u, err := url.Parse(ref); err == nil && u.Host == pr.In.Host {
			p := strings.TrimPrefix(u.EscapedPath(), prefix)
			if !strings.HasPrefix(p, "/") {
				p = "/" + p
			}
			if u.RawQuery != "" {
				p += "?" + u.RawQuery
			}
			pr.Out.Header.Set("Referer", self+p)
		}
	}
}
