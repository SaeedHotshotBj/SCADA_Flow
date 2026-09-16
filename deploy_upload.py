import os
import paramiko

SERVER_IP = "77.104.95.230"
USERNAME = "root"
PASSWORD = "I4Ql50K7KKIkZnhG"
REMOTE_PATH = "/var/www/scada"
LOCAL_PATH = os.path.dirname(os.path.abspath(__file__))

if not SERVER_IP:
    raise RuntimeError("SCADA_VPS_HOST is not set")
if not PASSWORD:
    raise RuntimeError("SCADA_VPS_PASSWORD is not set")

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


def mkdir_recursive(path):
    current = ""
    for folder in path.split("/"):
        if not folder:
            continue
        current += "/" + folder
        try:
            sftp.mkdir(current)
        except Exception:
            pass


def run_remote(command):
    stdin, stdout, stderr = ssh.exec_command(command)
    output = stdout.read().decode(errors="replace")
    error_output = stderr.read().decode(errors="replace")
    if output:
        print(output)
    if error_output:
        print(error_output)
    return stdout.channel.recv_exit_status(), output, error_output


def upload_folder(local, remote):
    ignored = {
        ".git",
        "__pycache__",
        "venv",
        "scada_flow.db",
        "scada_flow.db-wal",
        "scada_flow.db-shm",
    }
    for item in os.listdir(local):
        if item in ignored:
            continue
        local_item = os.path.join(local, item)
        remote_item = remote + "/" + item
        if os.path.isdir(local_item):
            mkdir_recursive(remote_item)
            upload_folder(local_item, remote_item)
        else:
            sftp.put(local_item, remote_item)


mkdir_recursive(REMOTE_PATH)
upload_folder(LOCAL_PATH, REMOTE_PATH)

permission_command = r'''
SERVICE_USER=$(systemctl show -p User --value scada); [ -z "$SERVICE_USER" ] && SERVICE_USER=root
SERVICE_GROUP=$(systemctl show -p Group --value scada); [ -z "$SERVICE_GROUP" ] && SERVICE_GROUP="$SERVICE_USER"
mkdir -p /var/www/scada/data
chown -R "$SERVICE_USER:$SERVICE_GROUP" /var/www/scada/data
chmod 775 /var/www/scada/data
[ -f /var/www/scada/data/scada_flow.db ] && chmod 664 /var/www/scada/data/scada_flow.db
[ -f /var/www/scada/data/scada_flow.db-wal ] && chmod 664 /var/www/scada/data/scada_flow.db-wal
[ -f /var/www/scada/data/scada_flow.db-shm ] && chmod 664 /var/www/scada/data/scada_flow.db-shm
'''
run_remote(permission_command)
run_remote("systemctl restart scada")

# Focused diagnostic only:
# PLC6 -> Company -> Flow -> TagMapper/DashboardOutput/MachineCard -> PLC_Data.
diagnostic = r'''cd /var/www/scada && .venv/bin/python - <<'PY'
import json
from database import get_connection, get_company_flow
from services.dashboard_service import get_dashboard_widgets

print("=== PLC6 DASHBOARD DIAGNOSTIC START ===")

conn = get_connection()
try:
    plc = conn.execute("""
        SELECT PLC_ID, CompanyID, PLC_Name
        FROM PLCs
        WHERE PLC_ID=6
    """).fetchone()
    print("PLC6=", dict(plc) if plc else None)
    if plc is None:
        raise SystemExit("PLC6 NOT FOUND")
    company_id = int(plc["CompanyID"])

    columns = [row[1] for row in conn.execute("PRAGMA table_info(PLC_Data)").fetchall()]
    print("PLC_DATA_COLUMNS=", columns)

    rows = conn.execute("""
        SELECT ID, CompanyID, PLC_ID, TagName, Value, StorageType, Timestamp
        FROM PLC_Data
        WHERE PLC_ID=6
        ORDER BY ID DESC
        LIMIT 20
    """).fetchall()
    print("PLC6_PLC_DATA_ROWS=", len(rows))
    for row in rows:
        print("PLC6_DATA=", dict(row))
finally:
    conn.close()

flow_json = get_company_flow(company_id)
flow = json.loads(flow_json or "{}")
nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}
print("COMPANY_ID=", company_id)

for node_id, node in nodes.items():
    if not isinstance(node, dict):
        continue
    name = node.get("name")
    if name not in {"PLCReader", "TagMapper", "DashboardOutput", "MachineCard"}:
        continue
    data = node.get("data", {}) or {}
    config = data.get("config") if isinstance(data.get("config"), dict) else data
    if name == "TagMapper":
        print("TAGMAPPER_MAPPINGS=", json.dumps(config.get("mappings", []), ensure_ascii=False))
    elif name == "DashboardOutput":
        print("DASHBOARD_WIDGETS=", json.dumps(config.get("widgets", []), ensure_ascii=False))
    elif name == "MachineCard":
        print("MACHINE_CARDS=", json.dumps(config.get("machines", []), ensure_ascii=False))
    else:
        print("PLC_READER=", node_id, json.dumps(config, ensure_ascii=False))

widgets = get_dashboard_widgets(company_id)
print("RESOLVED_WIDGETS=")
for widget in widgets:
    if isinstance(widget, dict) and widget.get("plc_id") == 6:
        print(json.dumps(widget, ensure_ascii=False))

conn = get_connection()
try:
    selected = [w for w in widgets if isinstance(w, dict) and w.get("plc_id") == 6 and w.get("tag")]
    print("PLC6_SELECTED_WIDGETS=", json.dumps(selected, ensure_ascii=False))
    for widget in selected:
        tag = str(widget.get("tag")).strip()
        row = conn.execute("""
            SELECT PLC_ID, TagName, Value, Timestamp
            FROM PLC_Data
            WHERE CompanyID=? AND PLC_ID=? AND LOWER(TagName)=LOWER(?)
            ORDER BY Timestamp DESC, ID DESC
            LIMIT 1
        """, (company_id, 6, tag)).fetchone()
        print("LATEST_WIDGET_ROW=", json.dumps({"tag": tag, "row": dict(row) if row else None}, ensure_ascii=False))
finally:
    conn.close()

print("=== PLC6 DASHBOARD DIAGNOSTIC END ===")
PY'''

print("Running focused PLC6 dashboard diagnostic...")
status, output, error_output = run_remote(diagnostic)
print("DIAGNOSTIC_EXIT=", status)

sftp.close()
ssh.close()
print("DEPLOYMENT FINISHED")
