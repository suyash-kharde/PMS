#!/bin/sh
set -e

CERT_DIR=/etc/nginx/certs
HTTPS_PORT="${HTTPS_PORT:-8443}"
CERT_HOST="${CERT_HOST:-localhost}"
# extra hostnames / IPs to put in the cert, comma- or space-separated (optional)
CERT_SAN="${CERT_SAN:-}"
# renew a self-signed cert this many seconds before it expires (default 30 days)
CERT_RENEW_BEFORE="${CERT_RENEW_BEFORE:-2592000}"
# set CERT_FORCE=1 to always regenerate the self-signed cert on boot
CERT_FORCE="${CERT_FORCE:-0}"

CRT="$CERT_DIR/server.crt"
KEY="$CERT_DIR/server.key"

mkdir -p "$CERT_DIR"

is_ip() { echo "$1" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; }

# build a subjectAltName string from CERT_HOST + localhost + CERT_SAN, sending
# IP literals in as IP: entries (browsers ignore DNS: entries for an IP literal)
SAN=""
san_seen=" "
add_san() {
  for h in "$@"; do
    [ -n "$h" ] || continue
    case "$san_seen" in *" $h "*) continue ;; esac
    san_seen="$san_seen$h "
    if is_ip "$h"; then SAN="${SAN:+$SAN,}IP:$h"; else SAN="${SAN:+$SAN,}DNS:$h"; fi
  done
}
add_san "$CERT_HOST" localhost 127.0.0.1
# shellcheck disable=SC2046
add_san $(echo "$CERT_SAN" | tr ',' ' ')

is_self_signed() {
  s=$(openssl x509 -in "$1" -noout -subject 2>/dev/null | sed 's/^subject= *//')
  i=$(openssl x509 -in "$1" -noout -issuer  2>/dev/null | sed 's/^issuer= *//')
  [ -n "$s" ] && [ "$s" = "$i" ]
}

cert_covers_host() {
  # CERT_HOST present in the SAN block (or CN as a fallback)
  openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null | grep -qF "$CERT_HOST" \
    || openssl x509 -in "$1" -noout -subject 2>/dev/null | grep -qF "CN = $CERT_HOST"
}

reason=""
if [ ! -f "$CRT" ] || [ ! -f "$KEY" ]; then
  reason="no certificate on disk"
elif [ "$CERT_FORCE" = "1" ]; then
  reason="CERT_FORCE=1"
elif ! is_self_signed "$CRT"; then
  reason=""   # an operator-installed (CA) cert — never touch it
  echo "[proxy] using the operator-provided certificate (not self-signed); leaving it untouched"
elif ! openssl x509 -in "$CRT" -noout -checkend "$CERT_RENEW_BEFORE" >/dev/null 2>&1; then
  reason="the self-signed cert has expired or is within ${CERT_RENEW_BEFORE}s of expiry"
elif ! cert_covers_host "$CRT"; then
  reason="the self-signed cert does not cover CERT_HOST=${CERT_HOST}"
fi

if [ -n "$reason" ]; then
  echo "[proxy] generating a self-signed certificate for ${CERT_HOST} (${reason})"
  echo "[proxy]   subjectAltName = ${SAN}"
  [ -f "$CRT" ] && cp "$CRT" "$CRT.bak.$(date +%s)" 2>/dev/null || true
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$KEY" -out "$CRT" \
    -subj "/C=US/ST=Dev/L=Dev/O=Planwise/CN=${CERT_HOST}" \
    -addext "subjectAltName=${SAN}" 2>/dev/null
  echo "[proxy] certificate written to ${CERT_DIR}"
else
  echo "[proxy] reusing the existing certificate"
fi

# bake the public HTTPS port into the redirect rule
sed "s/\${HTTPS_PORT}/${HTTPS_PORT}/g" /etc/nginx/templates/nginx.conf > /etc/nginx/conf.d/default.conf

nginx -t
exec nginx -g "daemon off;"
