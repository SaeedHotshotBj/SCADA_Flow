import paramiko

SERVER_IP = "77.104.95.230"
USERNAME = "root"
PASSWORD = "I4Ql50K7KKIkZnhG"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

ssh.connect(
    SERVER_IP,
    username=USERNAME,
    password=PASSWORD
)

commands = [

"python3 - <<'PY'\nimport sqlite3\nconn=sqlite3.connect('/var/www/scada/data/scada_flow.db')\ncur=conn.cursor()\ncur.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")\nprint('TABLES:')\nfor t in cur.fetchall():\n    print(t[0])\nconn.close()\nPY",

"python3 - <<'PY'\nimport sqlite3\nconn=sqlite3.connect('/var/www/scada/data/scada_flow.db')\ncur=conn.cursor()\ncur.execute(\"SELECT sql FROM sqlite_master WHERE type='table'\")\nprint('SCHEMA:')\nfor row in cur.fetchall():\n    print(row[0])\nconn.close()\nPY"

]

for cmd in commands:

    print("\n==============================")
    print("Running command")
    print("==============================")

    stdin, stdout, stderr = ssh.exec_command(cmd)

    print(stdout.read().decode())

    error = stderr.read().decode()
    if error:
        print("ERROR:")
        print(error)

ssh.close()