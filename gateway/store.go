package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"sync"
)

type App struct {
	Name        string `json:"name"`
	Target      string `json:"target"`
	Insecure    bool   `json:"insecure"`     // skip TLS verify to backend
	RewriteHTML bool   `json:"rewrite_html"` // rewrite absolute links in HTML/CSS
	RewriteJS   bool   `json:"rewrite_js"`   // rewrite JS string paths + inject runtime shim
	// IsolateStorage gives the app its own localStorage/sessionStorage namespace (needs rewrite_js).
	IsolateStorage bool `json:"isolate_storage"`
	// SyncTokens "accessCookie,refreshCookie" makes the app's mounts survive parallel requests
	// after an access token expired (see tokensync.go).
	SyncTokens string `json:"sync_tokens,omitempty"`
	// Mounts expose extra backends under /<name><path>/ (e.g. the API server the frontend calls
	// directly). Absolute URLs of the target found in HTML/JS/CSS are rewritten to that path.
	Mounts []Mount `json:"mounts,omitempty"`
	// JSPaths "files" prefixes only path literals that name files (scripts, styles, images, fonts,
	// /@vite/ and /@fs/ modules) and leaves every other "/x" alone: SPA route tables such as
	// { ADMIN: "/dashboard" } and API sub-paths stay intact. Calls made at run time (fetch, XHR,
	// WebSocket, element src/href) are still prefixed by the shim. Empty = prefix every path literal.
	JSPaths string `json:"js_paths,omitempty"`
	// ScriptName sends X-Script-Name: <public prefix> so apps that support running under a sub-path
	// (pgAdmin, many Flask/WSGI apps) build every URL with the prefix themselves. Use it with the
	// body rewrites off; redirects and cookies that already carry the prefix are left alone.
	ScriptName bool `json:"script_name,omitempty"`
	// Replace swaps exact text in the app's HTML, CSS and JavaScript after the other rewrites, for
	// addresses an app builds at run time, e.g. `${location.hostname}:8000` -> a mount path.
	Replace []Replacement `json:"replace,omitempty"`
}

type Replacement struct {
	From string `json:"from"`
	To   string `json:"to"`
}

type Mount struct {
	Path   string `json:"path"`   // "/_api"
	Target string `json:"target"` // "http://172.16.15.169:8039"
}

type mountProxy struct {
	path string
	h    *proxyHandler
}

type entry struct {
	app    App
	proxy  *proxyHandler
	mounts []mountProxy
}

var mountPathRe = regexp.MustCompile(`^/[a-z0-9_-]{1,32}$`)
var cookieNameRe = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

type Store struct {
	path string
	mu   sync.RWMutex
	apps map[string]*entry
}

var nameRe = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,31}$`)

func NewStore(path string) (*Store, error) {
	s := &Store{path: path, apps: map[string]*entry{}}
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return s, nil
	}
	if err != nil {
		return nil, err
	}
	var list []App
	if err := json.Unmarshal(b, &list); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	for _, a := range list {
		e, err := newEntry(a)
		if err != nil {
			return nil, fmt.Errorf("app %q: %w", a.Name, err)
		}
		s.apps[a.Name] = e
	}
	return s, nil
}

func newEntry(a App) (*entry, error) {
	if !nameRe.MatchString(a.Name) || a.Name == "_admin" {
		return nil, errors.New("name must match [a-z0-9][a-z0-9_-]{0,31}")
	}
	u, err := url.Parse(a.Target)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return nil, errors.New("target must be http(s)://host[:port][/path]")
	}
	if a.SyncTokens != "" {
		if _, ok := parseSyncNames(a.SyncTokens); !ok {
			return nil, errors.New("sync_tokens must be two different cookie names: accessCookie,refreshCookie")
		}
	}
	if a.JSPaths != "" && a.JSPaths != "files" {
		return nil, errors.New(`js_paths must be "" (all) or "files"`)
	}
	for _, r := range a.Replace {
		if r.From == "" || len(r.From) > 500 || len(r.To) > 500 {
			return nil, errors.New("replace: each 'from' must be 1-500 characters and 'to' at most 500")
		}
	}
	e := &entry{app: a, proxy: newProxy(a, u)}
	seen := map[string]bool{}
	for _, m := range a.Mounts {
		mu, err := url.Parse(m.Target)
		if !mountPathRe.MatchString(m.Path) || seen[m.Path] {
			return nil, errors.New("mount path must be unique and match /[a-z0-9_-]+")
		}
		if err != nil || (mu.Scheme != "http" && mu.Scheme != "https") || mu.Host == "" {
			return nil, errors.New("mount target must be http(s)://host[:port][/path]")
		}
		seen[m.Path] = true
		ma := a // API traffic: no body rewriting
		ma.RewriteHTML, ma.RewriteJS, ma.Replace = false, false, nil
		e.mounts = append(e.mounts, mountProxy{m.Path, newProxyAt(basePath+"/"+a.Name+m.Path, ma, mu, u)})
	}
	return e, nil
}

func (s *Store) Get(name string) *entry {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.apps[name]
}

func (s *Store) List() []App {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]App, 0, len(s.apps))
	for _, e := range s.apps {
		out = append(out, e.app)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func (s *Store) Put(a App) error {
	e, err := newEntry(a)
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	old := s.apps[a.Name]
	s.apps[a.Name] = e
	if err := s.saveLocked(); err != nil {
		if old != nil {
			s.apps[a.Name] = old
		} else {
			delete(s.apps, a.Name)
		}
		return err
	}
	return nil
}

func (s *Store) Delete(name string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	old, ok := s.apps[name]
	if !ok {
		return false, nil
	}
	delete(s.apps, name)
	if err := s.saveLocked(); err != nil {
		s.apps[name] = old
		return false, err
	}
	return true, nil
}

func (s *Store) saveLocked() error {
	list := make([]App, 0, len(s.apps))
	for _, e := range s.apps {
		list = append(list, e.app)
	}
	sort.Slice(list, func(i, j int) bool { return list[i].Name < list[j].Name })
	b, err := json.MarshalIndent(list, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, s.path)
}
