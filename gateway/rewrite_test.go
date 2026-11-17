package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"strings"
	"testing"
)

func TestRewriteCSSURLs(t *testing.T) {
	const p = "/sites/injectoplast"
	cases := map[string]string{
		// Angular component style embedded in a JS string
		`styles: ['.login-page {\n  background-image: url(/assets/images/bg.png);\n}']`: `styles: ['.login-page {\n  background-image: url(/sites/injectoplast/assets/images/bg.png);\n}']`,
		`background:url("/img/a.svg")`:                         `background:url("/sites/injectoplast/img/a.svg")`,
		`url(\"/img/a.svg\")`:                                  `url(\"/sites/injectoplast/img/a.svg\")`,
		`style="background: url('/x.png')"`:                    `style="background: url('/sites/injectoplast/x.png')"`,
		`url(/sites/injectoplast/assets/a.png)`:                `url(/sites/injectoplast/assets/a.png)`, // already prefixed
		`url(//cdn.example.com/a.png)`:                         `url(//cdn.example.com/a.png)`,          // other host
		`url(assets/a.png) url(data:image/png;base64,AAA)`:     `url(assets/a.png) url(data:image/png;base64,AAA)`,
		`const u = new URL(/^x/.source, base); obj.url(/re/g)`: `const u = new URL(/^x/.source, base); obj.url(/re/g)`, // JS, not CSS
	}
	for in, want := range cases {
		if got := string(rewriteCSSURLs([]byte(in), p)); got != want {
			t.Errorf("\n in: %s\ngot: %s\nwant: %s", in, got, want)
		}
	}
}

func TestRewriteJSKeepsWorking(t *testing.T) {
	const p = "/sites/app1"
	in := `fetch("/api/x"); navigate(['/login']); s = 'url(/bg.png)'`
	want := `fetch("/sites/app1/api/x"); navigate(['/login']); s = 'url(/sites/app1/bg.png)'`
	if got := string(rewriteJS([]byte(in), p)); got != want {
		t.Errorf("\ngot:  %s\nwant: %s", got, want)
	}
}

func TestMountOriginIsTheAppFrontend(t *testing.T) {
	basePath = "/sites"
	defer func() { basePath = "" }()
	app, _ := url.Parse("http://172.16.15.167:4205")
	api, _ := url.Parse("http://172.16.15.167:8051")
	in := httptest.NewRequest("POST", "https://planwise.augtrans.com/sites/inj/_api/api/users/login", nil)
	in.Host = "planwise.augtrans.com"
	in.Header.Set("Origin", "https://planwise.augtrans.com")
	in.Header.Set("Referer", "https://planwise.augtrans.com/sites/inj/login?x=1")
	pr := &httputil.ProxyRequest{In: in, Out: in.Clone(in.Context())}
	pr.SetURL(api)
	rewriteOriginHeaders(pr, app, "/sites/inj")
	if got := pr.Out.Header.Get("Origin"); got != "http://172.16.15.167:4205" {
		t.Errorf("Origin = %q", got)
	}
	if got := pr.Out.Header.Get("Referer"); got != "http://172.16.15.167:4205/login?x=1" {
		t.Errorf("Referer = %q", got)
	}
	in.Header.Set("Origin", "https://evil.example") // cross-site origins are passed on unchanged
	pr = &httputil.ProxyRequest{In: in, Out: in.Clone(in.Context())}
	rewriteOriginHeaders(pr, app, "/sites/inj")
	if got := pr.Out.Header.Get("Origin"); got != "https://evil.example" {
		t.Errorf("cross-site Origin rewritten to %q", got)
	}
}

func TestReplaceRunsLastAndOnlyOnTextBodies(t *testing.T) {
	a := App{Name: "fv", RewriteJS: true, Replace: []Replacement{{
		From: "`${window.location.protocol}//${window.location.hostname}:8000/api/v1`",
		To:   "`${window.location.origin}/sites/fv/_api/api/v1`",
	}}}
	in := "var environment = { apiUrl: `${window.location.protocol}//${window.location.hostname}:8000/api/v1` }; fetch(\"/x\")"
	resp := &http.Response{
		StatusCode: 200,
		Header:     http.Header{"Content-Type": {"application/javascript"}},
		Body:       io.NopCloser(strings.NewReader(in)),
	}
	if err := rewriteBody(resp, a, "/sites/fv"); err != nil {
		t.Fatal(err)
	}
	got, _ := io.ReadAll(resp.Body)
	want := "var environment = { apiUrl: `${window.location.origin}/sites/fv/_api/api/v1` }; fetch(\"/sites/fv/x\")"
	if string(got) != want {
		t.Errorf("\ngot:  %s\nwant: %s", got, want)
	}
	img := &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"image/png"}},
		Body: io.NopCloser(strings.NewReader("8000/api/v1"))}
	_ = rewriteBody(img, a, "/sites/fv")
	if b, _ := io.ReadAll(img.Body); string(b) != "8000/api/v1" {
		t.Errorf("binary body changed: %s", b)
	}
}

func TestJSPathsFilesKeepsRoutesAndAPISubpaths(t *testing.T) {
	const p = "/sites/fv"
	in := `import("/chunk-AB12CD34.js"); import x from "/@vite/client"; img = "/assets/logo.png";
var ROLE_HOME_ROUTES = { ADMIN: "/capacity-planning/dashboard", VENDOR: "/quality-control/vendor" };
router.navigateByUrl(target ?? "/forbidden"); if (url.includes("/auth/refresh/")) {}`
	want := `import("/sites/fv/chunk-AB12CD34.js"); import x from "/sites/fv/@vite/client"; img = "/sites/fv/assets/logo.png";
var ROLE_HOME_ROUTES = { ADMIN: "/capacity-planning/dashboard", VENDOR: "/quality-control/vendor" };
router.navigateByUrl(target ?? "/forbidden"); if (url.includes("/auth/refresh/")) {}`
	if got := string(rewriteJSMode([]byte(in), p, true)); got != want {
		t.Errorf("\ngot:  %s\nwant: %s", got, want)
	}
	// default mode is unchanged: every path literal still gets the prefix
	if got := string(rewriteJS([]byte(`a = "/capacity-planning/dashboard"`), p)); got != `a = "/sites/fv/capacity-planning/dashboard"` {
		t.Errorf("default mode changed: %s", got)
	}
}

func TestAlreadyPrefixedRedirectsAndCookiesAreKept(t *testing.T) {
	target, _ := url.Parse("http://172.16.15.167:4144")
	resp := &http.Response{Header: http.Header{
		"Location":   {"/sites/pg/login?next=/sites/pg/browser/"},
		"Set-Cookie": {"a=1; Path=/sites/pg/", "b=2; Path=/"},
	}}
	rewriteLocation(resp, "/sites/pg", "", target)
	rewriteCookies(resp, "/sites/pg", "")
	if got := resp.Header.Get("Location"); got != "/sites/pg/login?next=/sites/pg/browser/" {
		t.Errorf("Location = %q", got)
	}
	if got := resp.Header.Values("Set-Cookie"); got[0] != "a=1; Path=/sites/pg/" || got[1] != "b=2; Path=/sites/pg/" {
		t.Errorf("Set-Cookie = %q", got)
	}
}
