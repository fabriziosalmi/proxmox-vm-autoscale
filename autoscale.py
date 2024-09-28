#!/usr/bin/env python3

import time
import yaml
import logging
import json
from pathlib import Path
from vm_resource_manager import VMResourceManager
from host_resource_checker import HostResourceChecker
from ssh_utils import SSHClient
import requests

# Load configuration
CONFIG_PATH = "/usr/local/bin/vm_autoscale/config.yaml"

with open(CONFIG_PATH, 'r') as config_file:
    config = yaml.safe_load(config_file)

# Configure logging
logging_config_path = "/usr/local/bin/vm_autoscale/logging_config.json"
if Path(logging_config_path).exists():
    with open(logging_config_path, 'r') as logging_file:
        logging_config = json.load(logging_file)
        logging.config.dictConfig(logging_config)
else:
    logging.basicConfig(
        level=config['logging'].get('level', 'INFO'),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(config['logging']['log_file']), logging.StreamHandler()]
    )
logger = logging.getLogger("vm_autoscale")

# Gotify Notification
def send_gotify_notification(message, priority=5):
    if not config.get('gotify', {}).get('enabled', False):
        return

    server_url = config['gotify']['server_url']
    app_token = config['gotify']['app_token']
    priority = config['gotify'].get('priority', priority)

    try:
        response = requests.post(
            f"{server_url}/message",
            data={
                "title": "VM Autoscale Alert",
                "message": message,
                "priority": priority
            },
            headers={
                "Authorization": f"Bearer {app_token}"
            }
        )
        response.raise_for_status()
        logger.info("Gotify notification sent successfully")
    except Exception as e:
        logger.error(f"Failed to send Gotify notification: {str(e)}")

# Main autoscaling loop
def main():
    while True:
        for host in config['proxmox_hosts']:
            ssh_client = SSHClient(
                host=host['host'],
                user=host['ssh_user'],
                password=host.get('ssh_password'),
                key_path=host.get('ssh_key')
            )

            # Check VMs on the current Proxmox host
            for vm in config['virtual_machines']:
                if vm['proxmox_host'] != host['name']:
                    continue

                if not vm.get('scaling_enabled', False):
                    logger.info(f"Scaling not enabled for VM {vm['vm_id']}")
                    continue

                vm_manager = VMResourceManager(ssh_client, vm['vm_id'])
                host_checker = HostResourceChecker(ssh_client)

                try:
                    # Get VM resource usage
                    vm_cpu_usage, vm_ram_usage = vm_manager.get_resource_usage()
                    logger.info(f"VM {vm['vm_id']} - CPU Usage: {vm_cpu_usage}%, RAM Usage: {vm_ram_usage}%")

                    # Check host resource availability
                    if not host_checker.check_host_resources(
                            config['host_limits']['max_host_cpu_percent'],
                            config['host_limits']['max_host_ram_percent']):
                        logger.warning(f"Host resources are maxed out for {host['name']}. Scaling restricted.")
                        send_gotify_notification(f"Host {host['name']} resources maxed out. Scaling restricted.")
                        continue

                    # Scale VM based on thresholds
                    if vm['cpu_scaling']:
                        if vm_cpu_usage > config['scaling_thresholds']['cpu']['high']:
                            logger.info(f"Scaling up CPU for VM {vm['vm_id']}")
                            vm_manager.scale_cpu('up')
                            send_gotify_notification(f"Scaled up CPU for VM {vm['vm_id']} due to high usage.")

                        elif vm_cpu_usage < config['scaling_thresholds']['cpu']['low']:
                            logger.info(f"Scaling down CPU for VM {vm['vm_id']}")
                            vm_manager.scale_cpu('down')
                            send_gotify_notification(f"Scaled down CPU for VM {vm['vm_id']} due to low usage.")

                    if vm['ram_scaling']:
                        if vm_ram_usage > config['scaling_thresholds']['ram']['high']:
                            logger.info(f"Scaling up RAM for VM {vm['vm_id']}")
                            vm_manager.scale_ram('up')
                            send_gotify_notification(f"Scaled up RAM for VM {vm['vm_id']} due to high usage.")

                        elif vm_ram_usage < config['scaling_thresholds']['ram']['low']:
                            logger.info(f"Scaling down RAM for VM {vm['vm_id']}")
                            vm_manager.scale_ram('down')
                            send_gotify_notification(f"Scaled down RAM for VM {vm['vm_id']} due to low usage.")

                except Exception as e:
                    logger.error(f"Error processing VM {vm['vm_id']} on host {host['name']}: {str(e)}")
                    send_gotify_notification(f"Error processing VM {vm['vm_id']} on host {host['name']}: {str(e)}")

        time.sleep(config.get('check_interval', 60))

if __name__ == "__main__":
    main()
