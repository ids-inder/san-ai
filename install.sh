#!/usr/bin/env bash
# Install San AI on this server. Idempotent - safe to re-run to upgrade.
set -euo pipefail

PREFIX=${PREFIX:-/opt/sanai}
STATE=${STATE:-/var/lib/sanai}
ETC=${ETC:-/etc/sanai}
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[[ $EUID -eq 0 ]] || { echo "run as root (sudo ./install.sh)" >&2; exit 1; }

echo "==> dependencies"
if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip
elif command -v dnf >/dev/null; then
    dnf install -y -q python3 python3-pip
fi

echo "==> service user"
id -u sanai >/dev/null 2>&1 || useradd --system --home "$STATE" --shell /usr/sbin/nologin sanai

echo "==> directories"
mkdir -p "$PREFIX" "$STATE/work" "$ETC/tasks"
chown -R sanai:sanai "$STATE"
chmod 750 "$STATE"

echo "==> code"
# Copy the tree, excluding local artefacts.
tar -C "$SRC" --exclude=.git --exclude=.venv --exclude='__pycache__' \
    --exclude='*.pyc' -cf - . | tar -C "$PREFIX" -xf -

echo "==> virtualenv"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$PREFIX"

echo "==> configuration"
if [[ ! -f "$ETC/sanai.env" ]]; then
    TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(40))')
    sed -e "s|^SANAI_WEB_TOKEN=.*|SANAI_WEB_TOKEN=$TOKEN|" \
        -e "s|^SANAI_DB_PATH=.*|SANAI_DB_PATH=$STATE/sanai.db|" \
        -e "s|^SANAI_WORKDIR=.*|SANAI_WORKDIR=$STATE/work|" \
        "$SRC/.env.example" > "$ETC/sanai.env"
    chmod 640 "$ETC/sanai.env"
    chown root:sanai "$ETC/sanai.env"
    echo "    wrote $ETC/sanai.env with a generated dashboard token"
else
    echo "    kept existing $ETC/sanai.env"
fi

echo "==> systemd"
install -m644 "$SRC/deploy/sanai-web.service" /etc/systemd/system/
install -m644 "$SRC/deploy/sanai-task@.service" /etc/systemd/system/
systemctl daemon-reload

echo "==> command on PATH"
ln -sf "$PREFIX/venv/bin/sanai" /usr/local/bin/sanai

cat <<EOF

San AI installed.

  1. Add your API key:
       \$EDITOR $ETC/sanai.env        # set ANTHROPIC_API_KEY

  2. Check it works:
       set -a && . $ETC/sanai.env && set +a
       sanai doctor

  3. Teach it something:
       sanai learn "nginx reverse proxy" --probe \\
         --url https://nginx.org/en/docs/http/ngx_http_proxy_module.html

  4. Start the dashboard (loopback only):
       systemctl enable --now sanai-web
       grep SANAI_WEB_TOKEN $ETC/sanai.env

     Reach it over an SSH tunnel:
       ssh -L 8899:127.0.0.1:8899 root@$(hostname -I 2>/dev/null | awk '{print $1}')
     then open http://127.0.0.1:8899/?token=<token>

  Emergency stop at any time:
       sanai stop      # or: touch $STATE/STOP

EOF
