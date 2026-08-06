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

script = r'''
import sqlite3

db = "/var/www/scada/data/scada_flow.db"

conn = sqlite3.connect(db)
cur = conn.cursor()

tables = [
    "PLC_Data",
    "TagHistory",
    "AlarmHistory"
]

for table in tables:
    cur.execute(f"DELETE FROM {table}")
    print("Cleared:", table)

conn.commit()

conn.close()

print("DONE")
'''

# create temporary python file on server
sftp = ssh.open_sftp()

with sftp.file("/tmp/clear_db.py", "w") as f:
    f.write(script)

sftp.close()

# run it
stdin, stdout, stderr = ssh.exec_command(
    "python3 /tmp/clear_db.py"
)

print(stdout.read().decode())
print(stderr.read().decode())

ssh.close()