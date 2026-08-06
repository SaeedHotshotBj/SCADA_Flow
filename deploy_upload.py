import os
import paramiko

# ==============================
# SERVER SETTINGS
# ==============================

SERVER_IP = "77.104.95.230"
USERNAME = "root"
PASSWORD = "I4Ql50K7KKIkZnhG"

REMOTE_PATH = "/var/www/scada"
LOCAL_PATH = os.path.dirname(os.path.abspath(__file__))


# ==============================
# CONNECT
# ==============================

print("Connecting to server...")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

ssh.connect(
    SERVER_IP,
    username=USERNAME,
    password=PASSWORD,
    look_for_keys=False,
    allow_agent=False
)

sftp = ssh.open_sftp()

print("Connected")


# ==============================
# CREATE REMOTE DIRECTORY
# ==============================

def mkdir_recursive(path):
    folders = path.split("/")
    current = ""

    for folder in folders:
        if folder:
            current += "/" + folder
            try:
                sftp.mkdir(current)
            except:
                pass


mkdir_recursive(REMOTE_PATH)


# ==============================
# UPLOAD FILES
# ==============================

def upload_folder(local, remote):

    for item in os.listdir(local):

        local_item = os.path.join(local, item)
        remote_item = remote + "/" + item

        # ignore git files
        if item in [".git", "__pycache__", "venv"]:
            continue

        if os.path.isdir(local_item):

            print("DIR :", remote_item)

            mkdir_recursive(remote_item)

            upload_folder(
                local_item,
                remote_item
            )

        else:

            print("FILE:", item)

            sftp.put(
                local_item,
                remote_item
            )


upload_folder(
    LOCAL_PATH,
    REMOTE_PATH
)


print("")
print("==============================")
print("UPLOAD FINISHED")
print("==============================")


print("")
print("==============================")
print("UPLOAD FINISHED")
print("==============================")
print("Checking Nginx config...")

stdin, stdout, stderr = ssh.exec_command(
    "ls -la /etc/nginx/sites-enabled/ && echo '---' && nginx -T | grep -A20 -B5 server_name"
)
stdin, stdout, stderr = ssh.exec_command("systemctl status scada --no-pager")

print(stdout.read().decode())
print(stderr.read().decode())
stdin, stdout, stderr = ssh.exec_command(
    "journalctl -u scada -n 80 --no-pager"
)

print(stdout.read().decode())
print(stderr.read().decode())
print("Restarting SCADA service...")

stdin, stdout, stderr = ssh.exec_command(
    "systemctl restart scada"
)

exit_code = stdout.channel.recv_exit_status()

if exit_code == 0:
    print("SCADA service restarted successfully")
else:
    print("Restart failed:")
    print(stderr.read().decode())


sftp.close()
ssh.close()