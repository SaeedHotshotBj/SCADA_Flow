#!/usr/bin/env python3
"""
SCADA_FLOW — deploy to Ubuntu VPS via git bundle + SSH.

Reads SSH credentials from ~/.cursor/mcp.json (user-ssh-mcp or ssh-mcp).
Preserves production data/ directory (SQLite DB) on each deploy.

Usage:
  python scripts/vps-deploy.py
  python scripts/vps-deploy.py --force
  python scripts/vps-deploy.py --setup          # first-time nginx/systemd/SSL
  python scripts/vps-deploy.py --setup --force
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("Install paramiko: python -m pip install paramiko", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
MCP_CONFIG = Path.home() / ".cursor" / "mcp.json"

APP_DIR = "/var/www/scada"
DOMAIN = "scada.khze.org"
APP_PORT = 5000
SERVICE_NAME = "scada"
GIT_BRANCH = "main"
REMOTE_BUNDLE = "/var/www/deploy.bundle"
REMOTE_SETUP = "/tmp/scada-server-setup.sh"
REMOTE_DEPLOY = "/tmp/scada-deploy.sh"

TEMPLATES = Path(__file__).resolve().parent / "templates"


def load_ssh_config() -> dict:
    if not MCP_CONFIG.exists():
        raise SystemExit(f"MCP config not found: {MCP_CONFIG}")

    servers = json.loads(MCP_CONFIG.read_text(encoding="utf-8")).get("mcpServers", {})
    args = None
    for name in ("user-ssh-mcp", "ssh-mcp"):
        if name in servers:
            args = servers[name].get("args", [])
            break

    if not args:
        raise SystemExit("No user-ssh-mcp or ssh-mcp server in mcp.json")

    cfg: dict[str, str] = {}
    for arg in args:
        if arg.startswith("--") and "=" in arg:
            key, value = arg.split("=", 1)
            cfg[key.lstrip("-")] = value

    for key in ("host", "user", "password"):
        if key not in cfg:
            raise SystemExit(f"Missing SSH --{key} in MCP config")

    return cfg


def run_local(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd or ROOT, check=True)


def git_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def create_bundle(path: Path) -> None:
    run_local(["git", "bundle", "create", str(path), GIT_BRANCH])


def ssh_connect(cfg: dict) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        cfg["host"],
        port=int(cfg.get("port", 22)),
        username=cfg["user"],
        password=cfg["password"],
        timeout=120,
        banner_timeout=120,
        auth_timeout=120,
    )
    return client


def ssh_run(client: paramiko.SSHClient, command: str, sudo: bool = False) -> str:
    prefix = "sudo " if sudo else ""
    print(f"\n>>> {prefix}{command[:120]}{'...' if len(command) > 120 else ''}")
    stdin, stdout, stderr = client.exec_command(prefix + command, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print(err.rstrip(), file=sys.stderr)
    if exit_code != 0:
        raise SystemExit(f"Remote command failed ({exit_code})")
    return out


def upload_file(client: paramiko.SSHClient, local: Path, remote: str) -> None:
    sftp = client.open_sftp()
    sftp.put(str(local), remote)
    sftp.close()
    print(f"Uploaded {local.name} -> {remote}")


def upload_text(client: paramiko.SSHClient, content: str, remote: str) -> None:
    sftp = client.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(content)
    sftp.close()
    print(f"Uploaded script -> {remote}")


def build_setup_script() -> str:
    nginx_conf = (TEMPLATES / "nginx-scada.conf").read_text(encoding="utf-8")
    systemd_unit = (TEMPLATES / "scada.service").read_text(encoding="utf-8")

    return f"""#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "=== SCADA server setup ==="

apt-get update -qq
apt-get install -y -qq ca-certificates curl git python3-venv python3-pip nginx certbot python3-certbot-nginx

mkdir -p {APP_DIR}/data
chown -R www-data:www-data {APP_DIR} 2>/dev/null || true

cat > /etc/systemd/system/{SERVICE_NAME}.service <<'UNIT_EOF'
{systemd_unit}
UNIT_EOF

cat > /etc/nginx/sites-available/{DOMAIN} <<'NGINX_EOF'
{nginx_conf}
NGINX_EOF

ln -sf /etc/nginx/sites-available/{DOMAIN} /etc/nginx/sites-enabled/{DOMAIN}
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable {SERVICE_NAME} nginx

if [ ! -d /etc/letsencrypt/live/{DOMAIN} ]; then
  certbot --nginx -d {DOMAIN} \\
    --non-interactive --agree-tos \\
    --register-unsafely-without-email --redirect || echo "Certbot skipped (DNS/firewall?)"
fi

systemctl reload nginx || true
echo "=== Setup complete ==="
"""


def build_deploy_script() -> str:
    return f"""#!/bin/bash
set -euo pipefail

APP_DIR="{APP_DIR}"
APP_NEW="${{APP_DIR}}-new"
BACKUP="/tmp/scada-data-backup"
BUNDLE="{REMOTE_BUNDLE}"
BRANCH="{GIT_BRANCH}"

echo "=== SCADA deploy ==="

mkdir -p "$BACKUP"
if [ -d "$APP_DIR/data" ]; then
  rm -rf "$BACKUP"/*
  cp -a "$APP_DIR/data/." "$BACKUP/" || true
  echo "Backed up data/"
fi

rm -rf "$APP_NEW"
git clone -b "$BRANCH" "$BUNDLE" "$APP_NEW"

mkdir -p "$APP_NEW/data"
if [ -d "$BACKUP" ] && [ "$(ls -A "$BACKUP" 2>/dev/null || true)" ]; then
  cp -a "$BACKUP/." "$APP_NEW/data/"
  echo "Restored data/"
fi

rm -rf "$APP_DIR"
mv "$APP_NEW" "$APP_DIR"

cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

chown -R www-data:www-data "$APP_DIR"
mkdir -p "$APP_DIR/data"
chown -R www-data:www-data "$APP_DIR/data"

systemctl restart {SERVICE_NAME}
sleep 3

HTTP=$(curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{APP_PORT}/dashboard || echo "000")
echo "Local health check /dashboard -> HTTP $HTTP"

if [ "$HTTP" != "200" ]; then
  journalctl -u {SERVICE_NAME} -n 30 --no-pager || true
  exit 1
fi

echo "=== Deploy OK ==="
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy SCADA_FLOW to VPS")
    parser.add_argument("--force", action="store_true", help="Deploy even with uncommitted changes")
    parser.add_argument("--setup", action="store_true", help="First-time server setup (nginx/systemd/SSL)")
    args = parser.parse_args()

    if git_dirty() and not args.force:
        raise SystemExit(
            "Git working tree is dirty. Commit changes or use --force / deploy.ps1 -Force"
        )

    cfg = load_ssh_config()
    print(f"Target: {cfg['user']}@{cfg['host']} -> {APP_DIR}")
    print(f"Domain: https://{DOMAIN}")

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "deploy.bundle"
        create_bundle(bundle)

        client = ssh_connect(cfg)
        try:
            ssh_run(client, "mkdir -p /var/www")
            upload_file(client, bundle, REMOTE_BUNDLE)

            if args.setup:
                upload_text(client, build_setup_script(), REMOTE_SETUP)
                ssh_run(client, f"chmod +x {REMOTE_SETUP} && {REMOTE_SETUP}", sudo=True)

            upload_text(client, build_deploy_script(), REMOTE_DEPLOY)
            ssh_run(client, f"chmod +x {REMOTE_DEPLOY} && {REMOTE_DEPLOY}", sudo=True)

            if args.setup:
                https_code = ssh_run(
                    client,
                    f"curl -s -o /dev/null -w '%{{http_code}}' https://{DOMAIN}/dashboard || echo 000",
                ).strip()
                print(f"External HTTPS check: {https_code}")
        finally:
            client.close()

    print(f"\nDone. Open https://{DOMAIN}/dashboard")


if __name__ == "__main__":
    main()
