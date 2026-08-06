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

stdin, stdout, stderr = ssh.exec_command(
    "ls -la /var/www/scada/data"
)

print(stdout.read().decode())
print(stderr.read().decode())

ssh.close()