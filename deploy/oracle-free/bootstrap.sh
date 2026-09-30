#!/usr/bin/env bash
# Installs both SpecEngine APIs on an Ubuntu OCI Always Free VM.
# Run once from a newly provisioned VM:
#   curl -fsSL https://raw.githubusercontent.com/GumalAditya06/SpecEngin-bis/main/deploy/oracle-free/bootstrap.sh | sudo bash
set -euo pipefail

REPOSITORY="https://github.com/GumalAditya06/SpecEngin-bis.git"
INSTALL_ROOT="/opt/specengin"
SERVICE_USER="${SUDO_USER:-ubuntu}"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Could not determine the non-root login user. Run this with sudo from your normal SSH account." >&2
  exit 1
fi

apt-get update
apt-get install -y --no-install-recommends git nginx python3 python3-venv python3-pip

if [ -d "$INSTALL_ROOT/.git" ]; then
  sudo -u "$SERVICE_USER" git -C "$INSTALL_ROOT" pull --ff-only
else
  git clone "$REPOSITORY" "$INSTALL_ROOT"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_ROOT"
fi

sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_ROOT/backend/.venv"
sudo -u "$SERVICE_USER" "$INSTALL_ROOT/backend/.venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_ROOT/backend/.venv/bin/pip" install -e "$INSTALL_ROOT/backend" aiosqlite

sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_ROOT/scraper/.venv"
sudo -u "$SERVICE_USER" "$INSTALL_ROOT/scraper/.venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_ROOT/scraper/.venv/bin/pip" install -e "$INSTALL_ROOT/scraper[embeddings,service]"

install -d -m 0750 /etc/specengin
if [ ! -f /etc/specengin/catalogue.env ]; then
  printf '%s\n' 'BIS_DATABASE_URL=sqlite+aiosqlite:////opt/specengin/backend/local.db' > /etc/specengin/catalogue.env
fi
if [ ! -f /etc/specengin/assistant.env ]; then
  printf '%s\n' \
    'SPECENGINE_ENV=production' \
    'BIS_LLM_PROVIDER=gemini' \
    'BIS_LLM_MODEL=gemini-3.6-flash' \
    'BIS_LLM_API_KEY=PASTE_A_NEW_KEY_HERE' > /etc/specengin/assistant.env
  chmod 0600 /etc/specengin/assistant.env
  echo 'Set BIS_LLM_API_KEY in /etc/specengin/assistant.env before asking assistant questions.'
fi

sudo -u "$SERVICE_USER" env BIS_DATABASE_URL='sqlite+aiosqlite:////opt/specengin/backend/local.db' \
  "$INSTALL_ROOT/backend/.venv/bin/python" -m app.seed

cat > /etc/systemd/system/specengin-catalogue.service <<EOF
[Unit]
Description=SpecEngine catalogue API
After=network.target

[Service]
User=$SERVICE_USER
WorkingDirectory=$INSTALL_ROOT/backend
EnvironmentFile=/etc/specengin/catalogue.env
ExecStart=$INSTALL_ROOT/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/specengin-assistant.service <<EOF
[Unit]
Description=SpecEngine grounded-answer API
After=network.target

[Service]
User=$SERVICE_USER
WorkingDirectory=$INSTALL_ROOT/scraper
EnvironmentFile=/etc/specengin/assistant.env
ExecStart=$INSTALL_ROOT/scraper/.venv/bin/uvicorn scraper.assistant_api:app --host 127.0.0.1 --port 8001 --no-access-log
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/nginx/sites-available/specengin <<'EOF'
server {
    listen 80 default_server;
    server_name _;

    location /api/v1/assistant/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health/live {
        proxy_pass http://127.0.0.1:8001;
    }

    location /health/ready {
        proxy_pass http://127.0.0.1:8001;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/specengin /etc/nginx/sites-enabled/specengin
nginx -t
systemctl daemon-reload
systemctl enable --now specengin-catalogue specengin-assistant nginx

echo 'Installed. Test http://YOUR_VM_IP/api/v1/health and http://YOUR_VM_IP/health/ready.'
