package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httputil"
	"strings"
	"sync"
	"time"
)

// Some backends issue very short-lived access tokens and rotate the refresh token on first use,
// returning the new pair in Set-Cookie. A page that fires several requests at once after the
// access token expired then gets one 200 and several 401, because the other requests were
// already sent with the old cookies. token sync (App.SyncTokens = "accessCookie,refreshCookie")
// holds back requests that arrive while a refresh is in flight and sends them with the new
// cookie values. Only the two named cookies are touched; their values are never logged.

type tokenSession struct {
	mu      sync.Mutex
	access  string
	refresh string
}

type tokenStore struct {
	mu        sync.Mutex
	byRefresh map[string]*tokenSession
}

var tokens = &tokenStore{byRefresh: map[string]*tokenSession{}}

const maxTokenSessions = 4096

func (t *tokenStore) session(access, refresh string) *tokenSession {
	t.mu.Lock()
	defer t.mu.Unlock()
	if s, ok := t.byRefresh[refresh]; ok {
		return s
	}
	if len(t.byRefresh) >= maxTokenSessions {
		t.byRefresh = map[string]*tokenSession{} // sessions in flight keep their pointers
	}
	s := &tokenSession{access: access, refresh: refresh}
	t.byRefresh[refresh] = s
	return s
}

func (t *tokenStore) alias(refresh string, s *tokenSession) {
	t.mu.Lock()
	t.byRefresh[refresh] = s
	t.mu.Unlock()
}

type syncNames struct{ access, refresh string }

func parseSyncNames(v string) (*syncNames, bool) {
	parts := strings.Split(v, ",")
	if len(parts) != 2 {
		return nil, false
	}
	a, r := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
	if !cookieNameRe.MatchString(a) || !cookieNameRe.MatchString(r) || a == r {
		return nil, false
	}
	return &syncNames{a, r}, true
}

type syncKey struct{}

// syncCtx is per request. It holds the session lock while this request is the one that
// refreshes the tokens, and releases it as soon as the response headers arrive.
type syncCtx struct {
	names *syncNames
	s     *tokenSession
	held  bool
	once  sync.Once
}

func (c *syncCtx) release() {
	c.once.Do(func() {
		if c.held {
			c.s.mu.Unlock()
		}
	})
}

// prepare runs before the request is sent.
func (c *syncCtx) prepare(pr *httputil.ProxyRequest) {
	var access, refresh string
	for _, ck := range pr.In.Cookies() {
		switch ck.Name {
		case c.names.access:
			access = ck.Value
		case c.names.refresh:
			refresh = ck.Value
		}
	}
	if refresh == "" {
		return
	}
	s := tokens.session(access, refresh)
	s.mu.Lock()
	newestA, newestR := s.access, s.refresh
	if newestA != access || newestR != refresh {
		var parts []string
		for _, ck := range pr.In.Cookies() {
			v := ck.Value
			switch ck.Name {
			case c.names.access:
				v = newestA
			case c.names.refresh:
				v = newestR
			}
			parts = append(parts, ck.Name+"="+v)
		}
		pr.Out.Header.Set("Cookie", strings.Join(parts, "; "))
	}
	if !jwtExpired(newestA) {
		s.mu.Unlock()
		c.s = s
		return
	}
	// this request will trigger the refresh: keep others waiting until its headers come back
	c.s, c.held = s, true
}

// onResponse learns rotated tokens from Set-Cookie and releases the lock.
func (c *syncCtx) onResponse(resp *http.Response) {
	if c.s == nil {
		return
	}
	var na, nr string
	for _, ck := range resp.Cookies() {
		switch ck.Name {
		case c.names.access:
			na = ck.Value
		case c.names.refresh:
			nr = ck.Value
		}
	}
	if na != "" && nr != "" {
		if !c.held {
			c.s.mu.Lock()
			c.s.access, c.s.refresh = na, nr
			c.s.mu.Unlock()
		} else {
			c.s.access, c.s.refresh = na, nr
		}
		tokens.alias(nr, c.s)
	}
	c.release()
}

func jwtExpired(tok string) bool {
	parts := strings.Split(tok, ".")
	if len(parts) != 3 {
		return true // unknown format: be conservative and serialize
	}
	b, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if err != nil {
		return true
	}
	var p struct {
		Exp float64 `json:"exp"`
	}
	if json.Unmarshal(b, &p) != nil || p.Exp == 0 {
		return true
	}
	return time.Now().Unix() >= int64(p.Exp)-1
}

func withSync(r *http.Request, names *syncNames) (*http.Request, *syncCtx) {
	c := &syncCtx{names: names}
	return r.WithContext(context.WithValue(r.Context(), syncKey{}, c)), c
}

func syncFrom(ctx context.Context) *syncCtx {
	c, _ := ctx.Value(syncKey{}).(*syncCtx)
	return c
}
