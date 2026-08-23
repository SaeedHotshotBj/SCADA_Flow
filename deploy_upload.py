import os
import paramiko

# ============================================================
# SERVER SETTINGS
# ============================================================

SERVER_IP = "77.104.95.230"
USERNAME = "root"
PASSWORD = "I4Ql50K7KKIkZnhG"
REMOTE_PATH = "/var/www/scada"
LOCAL_PATH = os.path.dirname(os.path.abspath(__file__))

print("Connecting to server...")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    SERVER_IP,
    username=USERNAME,
    password=PASSWORD,
    look_for_keys=False,
    allow_agent=False,
)

sftp = ssh.open_sftp()
print("Connected")


def run_remote(command, title=None):
    if title:
        print()
        print(title)
        print("-" * 60)
    stdin, stdout, stderr = ssh.exec_command(command)
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    if output:
        print(output)
    if error:
        print(error)
    print("EXIT:", exit_code)
    return exit_code, output, error


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


mkdir_recursive(REMOTE_PATH)

# ============================================================
# FILES / DIRECTORIES TO NEVER DEPLOY
# ============================================================

IGNORED_ITEMS = {
    ".git",
    "__pycache__",
    "venv",
    "scada_flow.db",
    "scada_flow.db-wal",
    "scada_flow.db-shm",
}


# ============================================================
# UPLOAD FILES
# ============================================================

def upload_folder(local, remote):
    for item in os.listdir(local):
        if item in IGNORED_ITEMS:
            print("SKIP:", item)
            continue

        local_item = os.path.join(local, item)
        remote_item = remote + "/" + item

        if os.path.isdir(local_item):
            print("DIR :", remote_item)
            mkdir_recursive(remote_item)
            upload_folder(local_item, remote_item)
        else:
            print("FILE:", item)
            sftp.put(local_item, remote_item)


upload_folder(LOCAL_PATH, REMOTE_PATH)

# ============================================================
# FIX DATABASE DIRECTORY PERMISSIONS
# ============================================================

permission_command = r'''
SERVICE_USER=$(systemctl show -p User --value scada)
if [ -z "$SERVICE_USER" ]; then SERVICE_USER=root; fi

SERVICE_GROUP=$(systemctl show -p Group --value scada)
if [ -z "$SERVICE_GROUP" ]; then SERVICE_GROUP="$SERVICE_USER"; fi

mkdir -p /var/www/scada/data
chown -R "$SERVICE_USER:$SERVICE_GROUP" /var/www/scada/data
chmod 775 /var/www/scada/data

if [ -f /var/www/scada/data/scada_flow.db ]; then chmod 664 /var/www/scada/data/scada_flow.db; fi
if [ -f /var/www/scada/data/scada_flow.db-wal ]; then chmod 664 /var/www/scada/data/scada_flow.db-wal; fi
if [ -f /var/www/scada/data/scada_flow.db-shm ]; then chmod 664 /var/www/scada/data/scada_flow.db-shm; fi

echo "SERVICE_USER=$SERVICE_USER"
echo "SERVICE_GROUP=$SERVICE_GROUP"
echo "DATABASE PERMISSIONS:"
ls -la /var/www/scada/data
'''

run_remote(permission_command, "Fixing SQLite database permissions...")

# ============================================================
# CHECK PYTHON SYNTAX
# ============================================================

run_remote(
    "cd /var/www/scada && .venv/bin/python -m py_compile "
    "database.py app.py flow_runner.py services/trend_aggregation.py "
    "services/trend_runtime_fix.py",
    "Checking Python syntax...",
)

# ============================================================
# RESTART SCADA
# ============================================================

run_remote("systemctl restart scada", "Restarting SCADA service...")

# ============================================================
# CHECK SERVICE STATUS
# ============================================================

run_remote("systemctl status scada --no-pager -l", "Checking SCADA service...")

# ============================================================
# FORCE ONE TREND AGGREGATION PASS
# ============================================================

trend_command = r'''cd /var/www/scada && .venv/bin/python -c "
import sqlite3
from config import DB_CONFIG
from services.trend_runtime_fix import aggregate_once_local_time

conn = sqlite3.connect(DB_CONFIG['path'])
conn.row_factory = sqlite3.Row
latest = conn.execute(\"SELECT MAX(Timestamp) AS LatestTimestamp FROM PLC_Data WHERE (StorageType IS NULL OR UPPER(StorageType) IN ('EDGE','TIME'))\").fetchone()['LatestTimestamp']
print('LATEST_PLC_TIMESTAMP=', latest)
conn.close()

written = aggregate_once_local_time()
print('TREND_FORCE_WRITE=', written)

conn = sqlite3.connect(DB_CONFIG['path'])
print('TREND_MINUTE_COUNT=', conn.execute('SELECT COUNT(*) FROM TrendMinute').fetchone()[0])
print('TREND_MINUTE_LAST=', conn.execute('SELECT MAX(PeriodStart) FROM TrendMinute').fetchone()[0])
print('TREND_MINUTE_ROWS=', conn.execute('SELECT CompanyID, TagName, PeriodStart, PeriodEnd, FirstValue, LastValue, MinValue, MaxValue, WeightedAverage, DurationSeconds, SampleCount FROM TrendMinute ORDER BY PeriodStart DESC LIMIT 10').fetchall())
conn.close()
"'''

run_remote(trend_command, "Forcing one Trend aggregation pass...")

# ============================================================
# SHOW TREND LOGS
# ============================================================

run_remote(
    "journalctl -u scada -n 120 --no-pager -o cat | grep -i 'TREND AGGREGATION' || true",
    "Recent Trend aggregation logs...",
)

# ============================================================
# TEST FLASK LOCALLY
# ============================================================

run_remote(
    "curl -i --max-time 10 http://127.0.0.1:5000/",
    "Testing Flask locally...",
)

# ============================================================
# TEST PUBLIC DOMAIN
# ============================================================

run_remote(
    "curl -k -i --max-time 15 https://scada.khze.org/",
    "Testing public domain...",
)

# ============================================================
# SHOW RECENT SCADA LOGS
# ============================================================

run_remote(
    "journalctl -u scada -n 80 --no-pager -o cat",
    "Recent SCADA logs...",
)

sftp.close()
ssh.close()

print()
print("=" * 60)
print("DEPLOYMENT FINISHED")
print("=" * 60)
