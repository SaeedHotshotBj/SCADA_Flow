import os
import paramiko


# ============================================================
# SERVER SETTINGS
# ============================================================

SERVER_IP = "77.104.95.230"
USERNAME = "root"
PASSWORD = os.environ.get("SCADA_SSH_PASSWORD", "").strip()

REMOTE_PATH = "/var/www/scada"

LOCAL_PATH = os.path.dirname(
    os.path.abspath(__file__)
)

if not PASSWORD:
    raise RuntimeError(
        "SCADA_SSH_PASSWORD environment variable is not set."
    )


# ============================================================
# CONNECT
# ============================================================

print("Connecting to server...")

ssh = paramiko.SSHClient()

ssh.set_missing_host_key_policy(
    paramiko.AutoAddPolicy()
)

ssh.connect(
    SERVER_IP,
    username=USERNAME,
    password=PASSWORD,
    look_for_keys=False,
    allow_agent=False
)

sftp = ssh.open_sftp()

print("Connected")


# ============================================================
# CREATE REMOTE DIRECTORY
# ============================================================

def mkdir_recursive(path):

    folders = path.split("/")

    current = ""

    for folder in folders:

        if not folder:
            continue

        current += "/" + folder

        try:
            sftp.mkdir(current)

        except Exception:
            pass


mkdir_recursive(
    REMOTE_PATH
)


# ============================================================
# FILES / DIRECTORIES TO NEVER DEPLOY
# ============================================================

IGNORED_ITEMS = {
    ".git",
    "__pycache__",
    "venv",

    # SQLite runtime database files
    "scada_flow.db",
    "scada_flow.db-wal",
    "scada_flow.db-shm",
}


# ============================================================
# UPLOAD FILES
# ============================================================

def upload_folder(local, remote):

    for item in os.listdir(local):

        # ----------------------------------------------------
        # NEVER UPLOAD THESE
        # ----------------------------------------------------

        if item in IGNORED_ITEMS:

            print(
                "SKIP:",
                item
            )

            continue


        local_item = os.path.join(
            local,
            item
        )

        remote_item = (
            remote
            + "/"
            + item
        )


        # ----------------------------------------------------
        # DIRECTORY
        # ----------------------------------------------------

        if os.path.isdir(local_item):

            print(
                "DIR :",
                remote_item
            )

            mkdir_recursive(
                remote_item
            )

            upload_folder(
                local_item,
                remote_item
            )


        # ----------------------------------------------------
        # FILE
        # ----------------------------------------------------

        else:

            print(
                "FILE:",
                item
            )

            sftp.put(
                local_item,
                remote_item
            )


upload_folder(
    LOCAL_PATH,
    REMOTE_PATH
)


# ============================================================
# FIX DATABASE DIRECTORY PERMISSIONS
# ============================================================

print()
print(
    "Fixing SQLite database permissions..."
)


permission_command = r"""
SERVICE_USER=$(systemctl show -p User --value scada)

if [ -z "$SERVICE_USER" ]; then
    SERVICE_USER=root
fi

SERVICE_GROUP=$(systemctl show -p Group --value scada)

if [ -z "$SERVICE_GROUP" ]; then
    SERVICE_GROUP="$SERVICE_USER"
fi

mkdir -p /var/www/scada/data

chown -R "$SERVICE_USER:$SERVICE_GROUP" /var/www/scada/data

chmod 775 /var/www/scada/data

if [ -f /var/www/scada/data/scada_flow.db ]; then
    chmod 664 /var/www/scada/data/scada_flow.db
fi

if [ -f /var/www/scada/data/scada_flow.db-wal ]; then
    chmod 664 /var/www/scada/data/scada_flow.db-wal
fi

if [ -f /var/www/scada/data/scada_flow.db-shm ]; then
    chmod 664 /var/www/scada/data/scada_flow.db-shm
fi

echo "SERVICE_USER=$SERVICE_USER"
echo "SERVICE_GROUP=$SERVICE_GROUP"
echo "DATABASE PERMISSIONS:"
ls -la /var/www/scada/data
"""


stdin, stdout, stderr = ssh.exec_command(
    permission_command
)

print(
    stdout.read().decode()
)

error_output = stderr.read().decode()

if error_output:
    print(
        error_output
    )


# ============================================================
# CHECK PYTHON SYNTAX
# ============================================================

print()
print(
    "Checking Python syntax..."
)


stdin, stdout, stderr = ssh.exec_command(
    "cd /var/www/scada && "
    ".venv/bin/python -m py_compile "
    "database.py app.py flow_runner.py"
)

syntax_output = stdout.read().decode()
syntax_error = stderr.read().decode()

if syntax_output:
    print(
        syntax_output
    )

if syntax_error:
    print(
        syntax_error
    )


# ============================================================
# RESTART SCADA
# ============================================================

print()
print(
    "Restarting SCADA service..."
)


stdin, stdout, stderr = ssh.exec_command(
    "systemctl restart scada"
)

restart_error = stderr.read().decode()

exit_code = (
    stdout.channel.recv_exit_status()
)


if exit_code == 0:

    print(
        "SCADA service restarted successfully"
    )

else:

    print(
        "SCADA restart failed:"
    )

    print(
        restart_error
    )


# ============================================================
# CHECK SERVICE STATUS
# ============================================================

print()
print(
    "Checking SCADA service..."
)


stdin, stdout, stderr = ssh.exec_command(
    "systemctl status scada --no-pager -l"
)

print(
    stdout.read().decode()
)

status_error = stderr.read().decode()

if status_error:

    print(
        status_error
    )


# ============================================================
# SHOW RECENT LOGS
# ============================================================

print()
print(
    "Recent SCADA logs:"
)

print(
    "------------------------------------------------------------"
)


stdin, stdout, stderr = ssh.exec_command(
    "journalctl -u scada -n 40 --no-pager"
)

print(
    stdout.read().decode()
)

log_error = stderr.read().decode()

if log_error:

    print(
        log_error
    )


# ============================================================
# TEST FLASK LOCALLY
# ============================================================

print()
print("Testing Flask locally...")
print("------------------------------------------------------------")

stdin, stdout, stderr = ssh.exec_command(
    "curl -i --max-time 10 http://127.0.0.1:5000/"
)

local_test = stdout.read().decode()
local_error = stderr.read().decode()

print(local_test)

if local_error:
    print(local_error)


# ============================================================
# TEST PUBLIC DOMAIN
# ============================================================

print()
print("Testing public domain...")
print("------------------------------------------------------------")

stdin, stdout, stderr = ssh.exec_command(
    "curl -k -i --max-time 15 https://scada.khze.org/"
)

public_test = stdout.read().decode()
public_error = stderr.read().decode()

print(public_test)

if public_error:
    print(public_error)


# ============================================================
# SHOW ERROR GENERATED BY THE TEST REQUEST
# ============================================================

print()
print("Flask traceback after HTTP 500 test...")
print("------------------------------------------------------------")

stdin, stdout, stderr = ssh.exec_command(
    "journalctl -u scada -n 80 --no-pager -o cat"
)

print(stdout.read().decode())

error_output = stderr.read().decode()

if error_output:
    print(error_output)


# ============================================================
# CLOSE CONNECTION
# ============================================================

sftp.close()
ssh.close()


print()
print(
    "============================================================"
)

print(
    "DEPLOYMENT FINISHED"
)

print(
    "============================================================"
)
