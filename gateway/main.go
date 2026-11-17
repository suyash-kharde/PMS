package main

import (
	"crypto/rand"
	"crypto/subtle"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"html/template"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

//go:embed ui.html
var uiHTML []byte

// basePath is where the apps live on the public side, e.g. "/sites" when another proxy (Planwise's
// nginx) forwards https://planwise.example/sites/<app>/ here. Empty = apps at /<app>/.
var basePath string

type Server struct {
	store *Store
	token string
	admin http.Handler
}

func main() {
	listen := env("LISTEN", ":8443")
	dataDir := env("DATA_DIR", "./data")
	basePath = strings.TrimRight(os.Getenv("BASE_PATH"), "/")
	if basePath != "" && !strings.HasPrefix(basePath, "/") {
		basePath = "/" + basePath
	}
	token := os.Getenv("ADMIN_TOKEN")
	if token == "" {
		b := make([]byte, 16)
		_, _ = rand.Read(b)
		token = hex.EncodeToString(b)
		log.Printf("ADMIN_TOKEN not set; generated for this run: %s", token)
	}

	store, err := NewStore(filepath.Join(dataDir, "apps.json"))
	if err != nil {
		log.Fatal(err)
	}
	s := &Server{store: store, token: token}
	s.admin = s.adminMux()

	srv := &http.Server{
		Addr:              listen,
		Handler:           s,
		ReadHeaderTimeout: 15 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	if env("TLS", "true") == "true" {
		cert, key := os.Getenv("CERT_FILE"), os.Getenv("KEY_FILE")
		if cert == "" || key == "" {
			cert, key, err = ensureSelfSigned(filepath.Join(dataDir, "tls"), os.Getenv("TLS_HOSTS"))
			if err != nil {
				log.Fatal(err)
			}
		}
		log.Printf("gateway listening https on %s (admin: /_admin/)", listen)
		log.Fatal(srv.ListenAndServeTLS(cert, key))
	}
	log.Printf("gateway listening http on %s (admin: /_admin/)", listen)
	log.Fatal(srv.ListenAndServe())
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	p := r.URL.Path
	switch {
	case p == "/_admin":
		http.Redirect(w, r, "/_admin/", http.StatusMovedPermanently)
	case strings.HasPrefix(p, "/_admin/"):
		s.admin.ServeHTTP(w, r)
	case p == "/":
		s.landing(w, r)
	default:
		if basePath != "" {
			if !strings.HasPrefix(p, basePath+"/") {
				http.NotFound(w, r)
				return
			}
			p = p[len(basePath):]
		}
		name, _, _ := strings.Cut(p[1:], "/")
		e := s.store.Get(name)
		if e == nil {
			http.NotFound(w, r)
			return
		}
		if p == "/"+name {
			u := basePath + p + "/"
			if r.URL.RawQuery != "" {
				u += "?" + r.URL.RawQuery
			}
			http.Redirect(w, r, u, http.StatusMovedPermanently)
			return
		}
		rest := p[len(name)+1:]
		for _, m := range e.mounts {
			if rest == m.path || strings.HasPrefix(rest, m.path+"/") {
				m.h.ServeHTTP(w, r)
				return
			}
		}
		e.proxy.ServeHTTP(w, r)
	}
}

var landingTpl = template.Must(template.New("l").Parse(`<!doctype html><meta charset=utf-8>
<title>Gateway</title><style>body{font:16px system-ui;max-width:640px;margin:3rem auto;padding:0 1rem}li{margin:.4rem 0}</style>
<h1>Apps</h1><ul>{{range .Apps}}<li><a href="{{$.Base}}/{{.Name}}/">{{$.Base}}/{{.Name}}/</a></li>{{else}}<li>none yet</li>{{end}}</ul>
<p><a href="/_admin/">admin</a></p>`))

func (s *Server) landing(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = landingTpl.Execute(w, map[string]any{"Base": basePath, "Apps": s.store.List()})
}

func (s *Server) adminMux() http.Handler {
	m := http.NewServeMux()
	m.HandleFunc("GET /_admin/{$}", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write(uiHTML)
	})
	m.HandleFunc("GET /_admin/api/apps", s.auth(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, s.store.List())
	}))
	m.HandleFunc("POST /_admin/api/apps", s.auth(func(w http.ResponseWriter, r *http.Request) {
		var a App
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&a); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "bad json"})
			return
		}
		a.Name = strings.ToLower(strings.TrimSpace(a.Name))
		a.Target = strings.TrimSpace(a.Target)
		if err := s.store.Put(a); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, a)
	}))
	m.HandleFunc("DELETE /_admin/api/apps/{name}", s.auth(func(w http.ResponseWriter, r *http.Request) {
		ok, err := s.store.Delete(r.PathValue("name"))
		switch {
		case err != nil:
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		case !ok:
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "not found"})
		default:
			w.WriteHeader(http.StatusNoContent)
		}
	}))
	return m
}

func (s *Server) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if subtle.ConstantTimeCompare([]byte(got), []byte(s.token)) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
			return
		}
		next(w, r)
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}
