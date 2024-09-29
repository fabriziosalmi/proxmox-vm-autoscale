import logging
import json

class HostResourceChecker:
    def __init__(self, ssh_client):
        self.ssh_client = ssh_client
        self.logger = logging.getLogger("host_resource_checker")

    def check_host_resources(self, max_host_cpu_percent, max_host_ram_percent):
        try:
            command = "pvesh get /nodes/$(hostname)/status --output-format json"
            output = self.ssh_client.execute_command(command)

            data = json.loads(output)
            host_cpu_usage = data['cpu'] * 100  # Convert to percentage
            host_memory = data['memory']

            total_mem = host_memory['total']
            used_mem = host_memory['used']
            cached_mem = host_memory.get('cached', 0)
            free_mem = host_memory.get('free', 0)

            available_mem = free_mem + cached_mem
            host_ram_usage = ((total_mem - available_mem) / total_mem) * 100

            self.logger.info(f"Host CPU Usage: {host_cpu_usage:.2f}%, Host RAM Usage: {host_ram_usage:.2f}%")

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
