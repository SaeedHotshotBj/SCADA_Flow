import os
import paramiko

SERVER_IP = "77.104.95.230"
USERNAME = "root"
PASSWORD = "I4Ql50K7KKIkZnhG"
REMOTE_PATH = "/var/www/scada"


def run(command):
    stdin, stdout, stderr = ssh.exec_command(command)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    print("\n$", command)
    print(out)
    if err:
        print("STDERR:")
        print(err)
    print("EXIT:", code)
    return code, out, err


ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    SERVER_IP,
    username=USERNAME,
    password=PASSWORD,
    look_for_keys=False,
    allow_agent=False,
)

try:
    run(
        "cd /var/www/scada && .venv/bin/python -c "
        "\"from config import DB_CONFIG; print(DB_CONFIG)\""
    )

    run(
        "cd /var/www/scada && .venv/bin/python -c "
        "\"import sqlite3; from config import DB_CONFIG; "
        "p=DB_CONFIG['path']; c=sqlite3.connect(p); "
        "print('NOW=',c.execute(\\\"SELECT datetime('now','localtime')\\\").fetchone()[0]); "
        "print('PLC_DATA=',c.execute(\\\"SELECT COUNT(*), MAX(Timestamp), MIN(Timestamp) FROM PLC_Data\\\").fetchone()); "
        "print('TREND_MINUTE=',c.execute(\\\"SELECT COUNT(*), MAX(PeriodStart), MIN(PeriodStart) FROM TrendMinute\\\").fetchone()); "
        "print('STORAGE=',c.execute(\\\"SELECT StorageType, COUNT(*) FROM PLC_Data GROUP BY StorageType\\\").fetchall()); "
        "print('TAGS=',c.execute(\\\"SELECT CompanyID, TagName, COUNT(*), MAX(Timestamp) FROM PLC_Data GROUP BY CompanyID, TagName ORDER BY MAX(Timestamp) DESC LIMIT 20\\\").fetchall()); "
        "c.close()\""
    )

    run(
        "cd /var/www/scada && .venv/bin/python -c "
        "\"from services.trend_aggregation import aggregate_once; "
        "r=aggregate_once(); print('AGGREGATE_ONCE_RETURN=',r)\""
    )

    run(
        "cd /var/www/scada && .venv/bin/python -c "
        "\"import sqlite3; from config import DB_CONFIG; "
        "c=sqlite3.connect(DB_CONFIG['path']); "
        "print('TREND_MINUTE_AFTER=',c.execute(\\\"SELECT COUNT(*), MAX(PeriodStart), MIN(PeriodStart) FROM TrendMinute\\\").fetchone()); "
        "print('ROWS=',c.execute(\\\"SELECT CompanyID,TagName,PeriodStart,PeriodEnd,FirstValue,LastValue,MinValue,MaxValue,WeightedAverage,DurationSeconds,SampleCount FROM TrendMinute ORDER BY PeriodStart DESC LIMIT 20\\\").fetchall()); "
        "c.close()\""
    )

    run("journalctl -u scada -n 80 --no-pager -o cat | grep -i 'TREND' || true")
finally:
    ssh.close()
