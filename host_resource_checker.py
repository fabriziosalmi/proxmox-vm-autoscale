import logging
import json

class HostResourceChecker:
    def __init__(self, ssh_client):
        self.ssh_client = ssh_client
        self.logger = logging.getLogger("host_resource_checker")

    def check_host_resources(self, max_host_cpu_percent, max_host_ram_percent):
        """
        Check the host's available resources to ensure scaling is within capacity.
        :param max_host_cpu_percent: Maximum CPU usage percentage allowed for scaling.
        :param max_host_ram_percent: Maximum RAM usage percentage allowed for scaling.
        :return: True if the host has sufficient resources, False otherwise.
        """
        try:
            # Run `pvesh get /nodes/<node>/status` command to get host resource usage data
            command = "pvesh get /nodes/$(hostname)/status --output-format json"
            output = self.ssh_client.execute_command(command)
            
            # Parse JSON output
            data = json.loads(output)
            host_cpu_usage = data['cpu'] * 100   # Convert to percentage
            host_ram_usage = (data['memory']['used'] / data['memory']['total']) * 100  # Convert to percentage

            # Log current host resource status
            self.logger.info(f"Host CPU Usage: {host_cpu_usage:.2f}%, Host RAM Usage: {host_ram_usage:.2f}%")

            # Check if host resources are within acceptable limits
            if host_cpu_usage > max_host_cpu_percent:
                self.logger.warning(f"Host CPU usage exceeds maximum allowed limit ({host_cpu_usage:.2f}% > {max_host_cpu_percent}%)")
                return False

            if host_ram_usage > max_host_ram_percent:
                self.logger.warning(f"Host RAM usage exceeds maximum allowed limit ({host_ram_usage:.2f}% > {max_host_ram_percent}%)")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Failed to check host resources: {str(e)}")
            raise
