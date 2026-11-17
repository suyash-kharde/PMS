package main

import (
	"bytes"
	"io"
	"log"
	"mime"
	"mime/multipart"
	"net/http/httputil"
	"net/http"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// GW_DEBUG_AUTH=1 logs the *shape* of auth traffic (names and flags only, never values).
var debugAuth = os.Getenv("GW_DEBUG_AUTH") == "1"

var cookieAttrRe = regexp.MustCompile(`(?i);\s*(path|domain|max-age|expires|samesite|secure|httponly)(=[^;]*)?`)

func logAuthShape(app string, resp *http.Response) {
	req := resp.Request
	var reqCookies []string
	for _, c := range req.Cookies() {
		reqCookies = append(reqCookies, c.Name)
	}
	sort.Strings(reqCookies)
	var setCookies []string
	for _, c := range resp.Header.Values("Set-Cookie") {
		name := c
		if i := strings.IndexByte(c, '='); i > 0 {
			name = c[:i]
		}
		var attrs []string
		for _, m := range cookieAttrRe.FindAllStringSubmatch(c, -1) {
			a := strings.ToLower(m[1])
			if a == "path" || a == "max-age" || a == "samesite" {
				a += m[2]
			}
			attrs = append(attrs, a)
		}
		setCookies = append(setCookies, name+"["+strings.Join(attrs, ",")+"]")
	}
	var xh []string
	for k := range resp.Header {
		if strings.HasPrefix(strings.ToLower(k), "x-") {
			xh = append(xh, k)
		}
	}
	sort.Strings(xh)
	log.Printf("AUTHDBG %s %s %s -> %d | req: auth=%t refreshHdr=%t cookies=%v | resp: setCookie=%v xHeaders=%v",
		app, req.Method, req.URL.Path, resp.StatusCode,
		req.Header.Get("Authorization") != "", req.Header.Get("X-Refresh-Token") != "", reqCookies, setCookies, xh)
}

// peekErrorBody logs the first bytes of a 5xx body (debug only) and restores it.
func peekErrorBody(app string, resp *http.Response) {
	if resp.StatusCode < 500 || resp.Body == nil {
		return
	}
	head, _ := io.ReadAll(io.LimitReader(resp.Body, 600))
	resp.Body = struct {
		io.Reader
		io.Closer
	}{io.MultiReader(bytes.NewReader(head), resp.Body), resp.Body}
	log.Printf("AUTHDBG %s %s %s -> %d body: %q", app, resp.Request.Method, resp.Request.URL.Path, resp.StatusCode, string(head))
}

var secretNameRe = regexp.MustCompile(`(?i)pass|token|secret|auth|key`)

// peekMultipart logs the shape of a multipart upload (debug only) and restores the body.
func peekMultipart(app string, pr *httputil.ProxyRequest) {
	ct := pr.In.Header.Get("Content-Type")
	if !strings.HasPrefix(ct, "multipart/form-data") || pr.In.ContentLength > 12<<20 || pr.In.Body == nil {
		return
	}
	b, err := io.ReadAll(pr.In.Body)
	if err != nil {
		return
	}
	pr.Out.Body = io.NopCloser(bytes.NewReader(b))
	pr.Out.ContentLength = int64(len(b))
	_, params, err := mime.ParseMediaType(ct)
	if err != nil {
		return
	}
	var parts []string
	mr := multipart.NewReader(bytes.NewReader(b), params["boundary"])
	for {
		p, err := mr.NextPart()
		if err != nil {
			break
		}
		data, _ := io.ReadAll(p)
		if fn := p.FileName(); fn != "" {
			parts = append(parts, p.FormName()+"=FILE("+fn+","+p.Header.Get("Content-Type")+","+itoa(len(data))+"B)")
		} else if len(data) <= 120 && !secretNameRe.MatchString(p.FormName()) {
			parts = append(parts, p.FormName()+"="+string(data))
		} else {
			parts = append(parts, p.FormName()+"=<"+itoa(len(data))+"B>")
		}
	}
	log.Printf("AUTHDBG %s %s %s multipart total=%dB parts: %s", app, pr.In.Method, pr.In.URL.Path, len(b), strings.Join(parts, " | "))
}

func itoa(n int) string { return strconv.Itoa(n) }
