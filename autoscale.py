import yaml
import json
import requests
import smtplib
import logging
import logging.config
import time
from ssh_utils import SSHClient
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from vm_manager import VMResourceManager
from host_resource_checker import HostResourceChecker

# Load configuration
CONFIG_PATH = "/usr/local/bin/vm_autoscale/config.yaml"

if not Path(CONFIG_PATH).exists():
    raise FileNotFoundError(f"Configuration file not found at {CONFIG_PATH}")

with open(CONFIG_PATH, 'r') as config_file:
    config = yaml.safe_load(config_file)

# Configure logging
LOGGING_CONFIG_PATH = "/usr/local/bin/vm_autoscale/logging_config.json"
if Path(LOGGING_CONFIG_PATH).exists():
    with open(LOGGING_CONFIG_PATH, 'r') as logging_file:
        logging_config = json.load(logging_file)
        logging.config.dictConfig(logging_config)
else:
    logging.basicConfig(
        level=config.get('logging', {}).get('level', 'INFO'),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(config.get('logging', {}).get('log_file', '/var/log/vm_autoscale.log')),
            logging.StreamHandler()
        ]
    )
logger = logging.getLogger("vm_autoscale")

# Notification helpers
def send_notification(message, priority=5):
    if config.get('gotify', {}).get('enabled', False):
        send_gotify_notification(message, priority)
    elif config.get('smtp', {}).get('enabled', False):
        send_smtp_notification(message)
    else:
        logger.warning(f"No notification method is enabled. Message: {message}")

def send_gotify_notification(message, priority=5):
    gotify_config = config.get('gotify', {})
    server_url = gotify_config.get('server_url')
    app_token = gotify_config.get('app_token')
    priority = gotify_config.get('priority', priority)

    if not (server_url and app_token):
        logger.error("Gotify configuration is incomplete.")
        return

    try:
        response = requests.post(
            f"{server_url}/message",
            data={
                "title": "VM Autoscale Alert",
                "message": message,
                "priority": priority
            },
            headers={"Authorization": f"Bearer {app_token}"}
        )
        response.raise_for_status()
        logger.info("Gotify notification sent successfully")
    except Exception as e:
        logger.error(f"Failed to send Gotify notification: {e}")

def send_smtp_notification(message):
    smtp_config = config.get('smtp', {})
    smtp_host = smtp_config.get('server')
    smtp_port = smtp_config.get('port', 587)
    smtp_user = smtp_config.get('username')
    smtp_pass = smtp_config.get('password')
    from_email = smtp_config.get('from_email')
    to_emails = smtp_config.get('to_emails', [])

    if not all([smtp_host, smtp_user, smtp_pass, from_email, to_emails]):
        logger.error("Incomplete SMTP configuration.")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = ", ".join(to_emails)
        msg['Subject'] = "VM Autoscale Alert"
        msg.attach(MIMEText(message, 'plain'))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_emails, msg.as_string())

        logger.info("SMTP notification sent successfully")
    except Exception as e:
        logger.error(f"Failed to send SMTP notification: {e}")

# Main autoscaling logic
def process_vm(host, vm):
    ssh_client = None
    try:
        ssh_client = SSHClient(
            host=host['host'],
            user=host['ssh_user'],
            password=host.get('ssh_password'),
            key_path=host.get('ssh_key')
        )
        ssh_client.connect()

        vm_manager = VMResourceManager(ssh_client, vm['vm_id'], config)
        if not vm_manager.is_vm_running():
            logger.info(f"VM {vm['vm_id']} is not running. Skipping scaling.")
            return

        host_checker = HostResourceChecker(ssh_client)
        if not host_checker.check_host_resources(
                config['host_limits']['max_host_cpu_percent'],
                config['host_limits']['max_host_ram_percent']):
            logger.warning(f"Host {host['name']} resources maxed out. Scaling restricted.")
            send_notification(f"Host {host['name']} resources maxed out. Scaling restricted.")
            return

        # Scaling logic
        vm_cpu_usage, vm_ram_usage = vm_manager.get_resource_usage()
        logger.info(f"VM {vm['vm_id']} - CPU: {vm_cpu_usage}%, RAM: {vm_ram_usage}%")

        if vm['cpu_scaling']:
            if vm_cpu_usage > config['scaling_thresholds']['cpu']['high']:
                vm_manager.scale_cpu('up')
                send_notification(f"Scaled up CPU for VM {vm['vm_id']} due to high usage.")
            elif vm_cpu_usage < config['scaling_thresholds']['cpu']['low']:
                vm_manager.scale_cpu('down')
                send_notification(f"Scaled down CPU for VM {vm['vm_id']} due to low usage.")

        if vm['ram_scaling']:
            if vm_ram_usage > config['scaling_thresholds']['ram']['high']:
                vm_manager.scale_ram('up')
                send_notification(f"Scaled up RAM for VM {vm['vm_id']} due to high usage.")
            elif vm_ram_usage < config['scaling_thresholds']['ram']['low']:
                vm_manager.scale_ram('down')
                send_notification(f"Scaled down RAM for VM {vm['vm_id']} due to low usage.")

    except Exception as e:
        logger.error(f"Error processing VM {vm['vm_id']} on host {host['name']}: {e}")
        send_notification(f"Error processing VM {vm['vm_id']} on host {host['name']}: {e}")
    finally:
        if ssh_client:
            ssh_client.close()

def main():
    while True:
        for host in config['proxmox_hosts']:
            for vm in config['virtual_machines']:
                if vm['proxmox_host'] == host['name'] and vm.get('scaling_enabled', False):
                    process_vm(host, vm)
        time.sleep(config.get('check_interval', 60))

if __name__ == "__main__":
    main()
