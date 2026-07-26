#!/usr/bin/env bash
# Generate a self-signed TLS certificate for the LAN deployment (ADR-039).
#
# Why self-signed: the app is LAN-only and "never public", so there is no public DNS name for a real
# CA to validate. The alternative was plain HTTP, which puts the session cookie on the wire in clear
# text on a workshop network — the exact flaw the materials-costing review flagged (its cookie has no
# `secure` flag and Caddy serves port 80). Self-signed encrypts the transport; it does not prove
# identity, so a hostile device on the LAN could still MITM. Accepted for this threat model.
#
# Each workstation shows a one-time browser warning until the cert is trusted locally.
#
# Usage:
#   bin/make-cert.sh                       # 127.0.0.1 + localhost + this host's LAN IP
#   bin/make-cert.sh --host 192.168.1.50   # add another address/name (repeatable)
#   bin/make-cert.sh --days 825
#
# Writes certs/server.crt + certs/server.key (gitignored — a key must never be committed).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/certs"
DAYS=825          # the max most browsers accept for a leaf certificate
EXTRA_HOSTS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --host) EXTRA_HOSTS+=("${2:?--host needs a value}"); shift 2 ;;
        --days) DAYS="${2:?--days needs a value}"; shift 2 ;;
        --out)  OUT="${2:?--out needs a directory}"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

command -v openssl >/dev/null || { echo "openssl not found" >&2; exit 1; }

# Best-effort LAN address so the cert works from another workstation without being told the IP.
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)"

SAN="DNS:localhost,IP:127.0.0.1"
[ -n "$LAN_IP" ] && SAN="${SAN},IP:${LAN_IP}"
for host in ${EXTRA_HOSTS+"${EXTRA_HOSTS[@]}"}; do
    if printf '%s' "$host" | grep -qE '^[0-9]+(\.[0-9]+){3}$'; then
        SAN="${SAN},IP:${host}"
    else
        SAN="${SAN},DNS:${host}"
    fi
done

mkdir -p "$OUT"
CRT="${OUT}/server.crt"
KEY="${OUT}/server.key"

if [ -f "$CRT" ] || [ -f "$KEY" ]; then
    echo "Refusing to overwrite existing ${CRT} / ${KEY}." >&2
    echo "Delete them first if you really want to regenerate (every browser will re-warn)." >&2
    exit 1
fi

openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
    -keyout "$KEY" -out "$CRT" -days "$DAYS" \
    -subj "/CN=email-2-data" \
    -addext "subjectAltName=${SAN}" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" 2>/dev/null

chmod 600 "$KEY"
chmod 644 "$CRT"

echo "Certificate written:"
echo "  ${CRT}"
echo "  ${KEY}  (mode 600)"
echo "  valid ${DAYS} days, SAN: ${SAN}"
echo
echo "Serve with TLS:"
echo "  email2data serve --host 0.0.0.0 --tls-cert certs/server.crt --tls-key certs/server.key"
echo
echo "In Docker, mount certs/ read-only and set EMAIL2DATA_TLS_CERT / EMAIL2DATA_TLS_KEY."
