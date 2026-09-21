#!/usr/bin/env bash
# Stitch Doctor — idempotent installer for the Plesk host.
# Run as root from the deployed source directory:  ./install.sh
set -euo pipefail

SUBDOMAIN="stitchdr.threewalls.co.uk"
CONTAINER="stitchdoctor"
IMAGE="stitchdoctor"
PORT="8001"          # loopback port; nginx proxies the subdomain here
VOLUME="stitchdoctor-data"

echo "==> Stitch Doctor installer"

# 1. Docker must be up; port must be free (or already ours).
if ! docker info >/dev/null 2>&1; then
  echo "FATAL: Docker daemon is not running." >&2
  exit 1
fi

if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "--> existing container found; will replace it."
fi

if docker ps --format '{{.Ports}} {{.Names}}' | grep "127.0.0.1:${PORT}" | grep -qv "$CONTAINER"; then
  echo "FATAL: port ${PORT} is already used by another container." >&2
  docker ps --format '{{.Names}}\t{{.Ports}}' | grep "127.0.0.1:${PORT}"
  exit 1
fi

# 2. Create the subdomain (skip if it exists).
if plesk bin site --info "$SUBDOMAIN" >/dev/null 2>&1; then
  echo "--> subdomain $SUBDOMAIN already exists."
else
  echo "==> creating subdomain $SUBDOMAIN"
  plesk bin site --create "$SUBDOMAIN" -hosting true || {
    echo "WARNING: could not create subdomain automatically." >&2
    echo "         Create it in Plesk: Websites & Domains → Add Subdomain." >&2
  }
fi

# 3. Build the image.
echo "==> building image ${IMAGE}"
docker build -t "$IMAGE" .

# 4. Swap the container (named volume persists /data across restarts).
echo "==> swapping container ${CONTAINER} on 127.0.0.1:${PORT}"
docker rm -f "$CONTAINER" 2>/dev/null || true
docker run -d --name "$CONTAINER" \
  --restart unless-stopped \
  -p "127.0.0.1:${PORT}:8000" \
  -v "${VOLUME}:/data" \
  "$IMAGE"

# 5. Nginx proxy directives (50 MB cap; Plesk's 1 MB default breaks uploads).
VHOST_DIR="/var/www/vhosts/system/${SUBDOMAIN}/conf"
VHOST_FILE="${VHOST_DIR}/vhost_nginx.conf"
if [ -d "$VHOST_DIR" ]; then
  echo "==> writing nginx proxy directives"
  cat > "$VHOST_FILE" <<EOF
location / {
    proxy_pass http://127.0.0.1:${PORT};
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    client_max_body_size 50m;
}
EOF
  plesk sbin nginxmgt reload
else
  echo "WARNING: ${VHOST_DIR} not found — create the subdomain first, then re-run."
fi

# 6. Let's Encrypt certificate (skip if one is already installed).
if plesk bin site --info "$SUBDOMAIN" 2>/dev/null | grep -q "Certificate:"; then
  echo "--> certificate seems present; skipping issuance."
else
  echo "==> requesting Let's Encrypt certificate"
  plesk bin extension --exec letsencrypt --cli -d "$SUBDOMAIN" || {
    echo "WARNING: certificate issuance failed; run it again from the Plesk UI." >&2
  }
fi

# 7. Health check.
sleep 2
if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
  echo "==> container healthy on 127.0.0.1:${PORT}"
else
  echo "FATAL: container did not answer on ${PORT}." >&2
  docker logs --tail 30 "$CONTAINER"
  exit 1
fi

echo
echo "Done. Visit https://${SUBDOMAIN} once DNS + certificate are in place."
echo "Redeploy any time with: git pull && ./install.sh"
