#!/usr/bin/env python3
"""
SCADA_FLOW — deploy to Ubuntu VPS via git bundle + SSH.

Reads SSH credentials from ~/.cursor/mcp.json (user-ssh-mcp or ssh-mcp).
Preserves production data/ directory (SQLite DB) on each deploy.

ISOLATED DEPLOY — only touches:
  /var/www/scada/
  systemd unit: scada
  nginx vhost: scada.khze.org
  internal port: 5050

Does NOT modify other apps, nginx default site, or /var/www/* paths.

Usage:
  python scripts/vps-deploy.py
  python scripts/vps-deploy.py --force
  python scripts/vps-deploy.py --setup
  python scripts/vps-deploy.py --setup --force
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
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
APP_PORT = 5050  # internal port — avoid conflict with other apps on 5000/8000
SERVICE_NAME = "scada"
GIT_BRANCH = "main"
REMOTE_BUNDLE = "/var/www/scada/deploy.bundle"
REMOTE_SETUP = "/tmp/scada-server-setup.sh"
REMOTE_DEPLOY = "/tmp/scada-deploy.sh"
REMOTE_CONFIG = "/tmp/scada-config-refresh.sh"
REMOTE_LOG = "/tmp/scada-deploy.log"
REMOTE_PID = "/tmp/scada-deploy.pid"

TEMPLATES = Path(__file__).resolve().parent / "templates"
DEPLOY_POLL_SECONDS = 5
DEPLOY_MAX_WAIT = 900


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
    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(15)
    return client


def ssh_run(
    client: paramiko.SSHClient,
    command: str,
    *,
    sudo: bool = False,
    check: bool = True,
) -> tuple[str, int]:
    prefix = "sudo " if sudo else ""
    print(f"\n>>> {prefix}{command[:140]}{'...' if len(command) > 140 else ''}")

    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(15)

    _, stdout, stderr = client.exec_command(prefix + command, get_pty=False)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()

    if out.strip():
        print(out.rstrip())
    if err.strip():
        print(err.rstrip(), file=sys.stderr)

    if check and exit_code != 0:
        raise SystemExit(f"Remote command failed ({exit_code})")

    return out, exit_code


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

echo "=== SCADA setup (isolated — other apps untouched) ==="

# Only install missing packages; do not upgrade/reconfigure other services
for pkg in python3-venv python3-pip curl git; do
  dpkg -s "$pkg" >/dev/null 2>&1 || apt-get install -y -qq "$pkg"
done

mkdir -p {APP_DIR}/data

# systemd: only scada unit
cat > /etc/systemd/system/{SERVICE_NAME}.service <<'UNIT_EOF'
{systemd_unit}
UNIT_EOF

# nginx: only scada.khze.org vhost — do NOT remove default or other sites
cat > /etc/nginx/sites-available/{DOMAIN} <<'NGINX_EOF'
{nginx_conf}
NGINX_EOF

ln -sf /etc/nginx/sites-available/{DOMAIN} /etc/nginx/sites-enabled/{DOMAIN}

nginx -t
systemctl daemon-reload
systemctl enable {SERVICE_NAME}

# SSL only for scada domain (does not edit other vhosts)
if [ ! -d /etc/letsencrypt/live/{DOMAIN} ]; then
  if command -v certbot >/dev/null 2>&1; then
    certbot --nginx -d {DOMAIN} \\
      --non-interactive --agree-tos \\
      --register-unsafely-without-email --redirect || echo "Certbot skipped"
  else
    echo "certbot not installed — configure SSL manually for {DOMAIN}"
  fi
fi

# reload nginx if already running (shared service, configs unchanged for other sites)
if systemctl is-active nginx >/dev/null 2>&1; then
  nginx -t && systemctl reload nginx
fi

echo "=== SCADA setup complete ==="
echo "Touched only: {APP_DIR}, {SERVICE_NAME}.service, nginx site {DOMAIN}"
"""


def build_config_refresh_script() -> str:
    nginx_conf = (TEMPLATES / "nginx-scada.conf").read_text(encoding="utf-8")
    systemd_unit = (TEMPLATES / "scada.service").read_text(encoding="utf-8")

    return f"""#!/bin/bash
set -euo pipefail
cat > /etc/systemd/system/{SERVICE_NAME}.service <<'UNIT_EOF'
{systemd_unit}
UNIT_EOF
cat > /etc/nginx/sites-available/{DOMAIN} <<'NGINX_EOF'
{nginx_conf}
NGINX_EOF
ln -sf /etc/nginx/sites-available/{DOMAIN} /etc/nginx/sites-enabled/{DOMAIN}
systemctl daemon-reload
nginx -t && systemctl reload nginx
"""


def build_deploy_script() -> str:
    return f"""#!/bin/bash
set -euo pipefail

APP_DIR="{APP_DIR}"
APP_NEW="${{APP_DIR}}-new"
APP_OLD="${{APP_DIR}}-old"
BACKUP="/tmp/scada-data-backup"
BUNDLE="{REMOTE_BUNDLE}"
BRANCH="{GIT_BRANCH}"
LOG="{REMOTE_LOG}"

exec > >(tee -a "$LOG") 2>&1

echo "=== SCADA deploy $(date -Is) ==="

systemctl stop {SERVICE_NAME} 2>/dev/null || true
rm -rf "$APP_NEW" "$APP_OLD"

mkdir -p "$BACKUP"
if [ -d "$APP_DIR/data" ]; then
  rm -rf "$BACKUP"/*
  cp -a "$APP_DIR/data/." "$BACKUP/" || true
  echo "[1/6] Backed up data/"
fi

rm -rf "$APP_NEW"
git clone -b "$BRANCH" "$BUNDLE" "$APP_NEW"
echo "[2/6] Cloned bundle"

mkdir -p "$APP_NEW/data"
if [ -d "$BACKUP" ] && [ "$(ls -A "$BACKUP" 2>/dev/null || true)" ]; then
  cp -a "$BACKUP/." "$APP_NEW/data/"
  echo "[3/6] Restored data/"
fi

rm -rf "$APP_OLD"
if [ -d "$APP_DIR" ]; then
  mv "$APP_DIR" "$APP_OLD"
fi
mv "$APP_NEW" "$APP_DIR"
echo "[4/6] Swapped app directory"

cd "$APP_DIR"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "[5/6] Python deps installed"

mkdir -p "$APP_DIR/data"
chown -R www-data:www-data "$APP_DIR"
echo "[6/6] Permissions set"

systemctl start {SERVICE_NAME}
sleep 4

HTTP=$(curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{APP_PORT}/dashboard || echo "000")
echo "Health check /dashboard -> HTTP $HTTP"

if [ "$HTTP" != "200" ]; then
  journalctl -u {SERVICE_NAME} -n 40 --no-pager || true
  exit 1
fi

rm -rf "$APP_OLD"
echo "=== Deploy OK ==="
"""


def run_remote_deploy(cfg: dict) -> None:
    """Start deploy in background and poll log with fresh SSH connections."""

    client = ssh_connect(cfg)
    try:
        upload_text(client, build_deploy_script(), REMOTE_DEPLOY)
        ssh_run(
            client,
            f"rm -f {REMOTE_LOG} {REMOTE_PID} && "
            f"nohup sudo bash {REMOTE_DEPLOY} > {REMOTE_LOG} 2>&1 & "
            f"echo $! > {REMOTE_PID} && cat {REMOTE_PID}",
        )
    finally:
        client.close()

    print("\nDeploy running on server (background)...")
    deadline = time.time() + DEPLOY_MAX_WAIT
    last_line = ""

    while time.time() < deadline:
        time.sleep(DEPLOY_POLL_SECONDS)
        client = ssh_connect(cfg)
        try:
            log_out, _ = ssh_run(
                client,
                f"tail -n 25 {REMOTE_LOG} 2>/dev/null || true",
                check=False,
            )
            running_out, _ = ssh_run(
                client,
                f"if [ -f {REMOTE_PID} ] && kill -0 $(cat {REMOTE_PID}) 2>/dev/null; then echo running; else echo stopped; fi",
                check=False,
            )

            lines = [line for line in log_out.strip().splitlines() if line.strip()]
            if lines and lines[-1] != last_line:
                print("\n--- server log ---")
                print(log_out.rstrip())
                last_line = lines[-1]

            if "=== Deploy OK ===" in log_out:
                return

            if "stopped" in running_out:
                if "=== Deploy OK ===" in log_out:
                    return
                tail = log_out[-2000:] if log_out else "(empty log)"
                raise SystemExit(f"Deploy failed on server. Last log:\n{tail}")
        finally:
            client.close()

    raise SystemExit(f"Deploy timed out after {DEPLOY_MAX_WAIT}s. Check {REMOTE_LOG} on server.")


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
            ssh_run(client, f"mkdir -p {APP_DIR}")
            upload_file(client, bundle, REMOTE_BUNDLE)

            # refresh only SCADA nginx/systemd configs (safe for co-hosted apps)
            upload_text(client, build_config_refresh_script(), REMOTE_CONFIG)
            ssh_run(client, f"bash {REMOTE_CONFIG}", sudo=True)

            if args.setup:
                upload_text(client, build_setup_script(), REMOTE_SETUP)
                ssh_run(client, f"chmod +x {REMOTE_SETUP} && bash {REMOTE_SETUP}", sudo=True)
        finally:
            client.close()

        run_remote_deploy(cfg)

        if args.setup:
            client = ssh_connect(cfg)
            try:
                https_out, _ = ssh_run(
                    client,
                    f"curl -s -o /dev/null -w '%{{http_code}}' https://{DOMAIN}/dashboard || echo 000",
                    check=False,
                )
                print(f"External HTTPS check: {https_out.strip()}")
            finally:
                client.close()

    print(f"\nDone. Open https://{DOMAIN}/dashboard")


if __name__ == "__main__":
    main()
