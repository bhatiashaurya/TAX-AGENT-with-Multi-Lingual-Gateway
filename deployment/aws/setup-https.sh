#!/usr/bin/env bash
#
# One-shot HTTPS for Tax Agent on an Ubuntu EC2 instance.
# Installs Caddy, points it at the app on localhost:8080, and serves it over
# HTTPS via a nip.io hostname (no DNS signup — the hostname encodes the public
# IP, and Caddy fetches a real Let's Encrypt certificate for it).
#
# HTTPS is REQUIRED for microphone / voice input: browsers block the mic on
# plain http://. Text chat and the multilingual gateway work either way.
#
# Prereqs: security-group inbound ports 80 and 443 open (plus the app's 8080).
# Usage:   bash setup-https.sh
#
set -euo pipefail

echo "==> Detecting this instance's public IP..."
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 120" 2>/dev/null || true)
IP=""
if [ -n "${TOKEN:-}" ]; then
  IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
    http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)
fi
[ -z "$IP" ] && IP=$(curl -s https://checkip.amazonaws.com 2>/dev/null || true)
IP=$(echo -n "$IP" | tr -d '[:space:]')
if [ -z "$IP" ]; then
  echo "ERROR: could not detect public IP. Set HOST manually and re-run." >&2
  exit 1
fi
HOST="${IP}.nip.io"
echo "    Public IP : $IP"
echo "    HTTPS host: https://$HOST"

if ! command -v caddy >/dev/null 2>&1; then
  echo "==> Installing Caddy..."
  sudo apt-get update -y
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y caddy
else
  echo "==> Caddy already installed."
fi

echo "==> Writing /etc/caddy/Caddyfile..."
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
${HOST} {
    encode gzip
    # flush_interval -1 keeps the SSE chat token stream unbuffered.
    reverse_proxy localhost:8080 {
        flush_interval -1
    }
}
EOF

echo "==> Enabling and restarting Caddy..."
sudo systemctl enable caddy >/dev/null 2>&1 || true
sudo systemctl restart caddy
sleep 3
sudo systemctl --no-pager --lines=4 status caddy || true

cat <<EOF

======================================================================
 Done.  Open:  https://${HOST}
 First load may take ~15s while Caddy obtains the TLS certificate.
 Ensure security-group inbound ports 80 and 443 are open.
 The mic will work on the https:// URL (secure context).
======================================================================
EOF
