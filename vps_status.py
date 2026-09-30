"""Read-only VPS diagnostic for SCADA_FLOW.

This script never uploads files, changes the database, or restarts services.
It reuses the SSH credentials already present in deploy_upload.py by parsing
that file as plain Python source; deploy_upload.py is never imported/executed.
Environment variables may override the connection settings:
SCADA_VPS_HOST, SCADA_VPS_USER, SCADA_VPS_PASSWORD.
"""

import ast
import os
import sys

import paramiko


REMOTE_COMMANDS = [
    ("SERVICE_ACTIVE", "systemctl is-active scada"),
    ("SERVICE_STATUS", "systemctl status scada --no-pager -l"),
    (
        "SERVICE_LOG",
        "journalctl -u scada -n 150 --no-pager",
    ),
    (
        "LISTENING_PORTS",
        "ss -lntp",
    ),
]


def _credentials_from_deploy_script():
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "deploy_upload.py",
    )

    if not os.path.exists(path):
        raise FileNotFoundError("deploy_upload.py not found")

    source = open(path, "r", encoding="utf-8").read()
    tree = ast.parse(source, filename=path)

    values = {}
    wanted = {"SERVER_IP", "USERNAME", "PASSWORD"}

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id not in wanted:
                continue

            try:
                values[target.id] = ast.literal_eval(node.value)
            except Exception:
                pass

    return (
        values.get("SERVER_IP"),
        values.get("USERNAME"),
        values.get("PASSWORD"),
    )


def get_credentials():
    host = os.environ.get("SCADA_VPS_HOST", "").strip()
    username = os.environ.get("SCADA_VPS_USER", "").strip()
    password = os.environ.get("SCADA_VPS_PASSWORD", "")

    if not host or not username or not password:
        file_host, file_user, file_password = _credentials_from_deploy_script()
        host = host or str(file_host or "").strip()
        username = username or str(file_user or "").strip()
        password = password or str(file_password or "")

    if not host:
        raise RuntimeError("SCADA_VPS_HOST is not configured")
    if not username:
        raise RuntimeError("SCADA_VPS_USER is not configured")
    if not password:
        raise RuntimeError("SCADA_VPS_PASSWORD is not configured")

    return host, username, password


def run_remote(ssh, label, command):
    print(f"=== {label} ===")
    stdin, stdout, stderr = ssh.exec_command(command)
    output = stdout.read().decode(errors="replace")
    error_output = stderr.read().decode(errors="replace")

    if output:
        print(output.rstrip())

    if error_output:
        print(error_output.rstrip())

    print(f"[exit={stdout.channel.recv_exit_status()}]")
    print()


def main():
    host, username, password = get_credentials()

    print("Connecting to VPS for READ-ONLY diagnostics...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(
            host,
            username=username,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
        )
        print("Connected")
        print()

        for label, command in REMOTE_COMMANDS:
            run_remote(ssh, label, command)

    except Exception as exc:
        print("VPS DIAGNOSTIC ERROR:", type(exc).__name__, exc)
        sys.exit(1)
    finally:
        try:
            ssh.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
