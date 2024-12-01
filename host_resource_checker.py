import logging
import json

class HostResourceChecker:
    """
    Class to check and monitor host resource usage via SSH.
    """

    def __init__(self, ssh_client):
        """
        Initialize the HostResourceChecker with an SSH client.
        :param ssh_client: Instance of SSH client for executing remote commands.
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger("host_resource_checker")

    def check_host_resources(self, max_host_cpu_percent, max_host_ram_percent):
        """
        Check host CPU and RAM usage against specified thresholds.
        :param max_host_cpu_percent: Maximum allowable CPU usage percentage.
        :param max_host_ram_percent: Maximum allowable RAM usage percentage.
        :return: True if resources are within limits, False otherwise.
        """
        try:
            # Command to retrieve host resource status
            command = "pvesh get /nodes/$(hostname)/status --output-format json"
            output = self.ssh_client.execute_command(command)

            # Parse JSON response
            data = json.loads(output)

            # Validate required keys in the response
            if 'cpu' not in data or 'memory' not in data:
                raise KeyError("Missing 'cpu' or 'memory' in the command output.")

            # Extract and calculate CPU usage
            host_cpu_usage = data['cpu'] * 100  # Convert to percentage

            # Extract memory details
            memory_data = data['memory']
            total_mem = memory_data.get('total', 1)  # Avoid division by zero
            used_mem = memory_data.get('used', 0)
            cached_mem = memory_data.get('cached', 0)
            free_mem = memory_data.get('free', 0)

            # Calculate RAM usage as a percentage
            available_mem = free_mem + cached_mem
            host_ram_usage = ((total_mem - available_mem) / total_mem) * 100

            # Log resource usage
            self.logger.info(f"Host CPU Usage: {host_cpu_usage:.2f}%, "
                             f"Host RAM Usage: {host_ram_usage:.2f}%")

            # Check CPU usage threshold
            if host_cpu_usage > max_host_cpu_percent:
                self.logger.warning(f"Host CPU usage exceeds maximum allowed limit: "
                                    f"{host_cpu_usage:.2f}% > {max_host_cpu_percent}%")
                return False

            # Check RAM usage threshold
            if host_ram_usage > max_host_ram_percent:
                self.logger.warning(f"Host RAM usage exceeds maximum allowed limit: "
                                    f"{host_ram_usage:.2f}% > {max_host_ram_percent}%")
                return False

            # Resources are within limits
            return True

        except json.JSONDecodeError as json_err:
            self.logger.error(f"Failed to parse JSON output: {str(json_err)}")
            raise
        except KeyError as key_err:
            self.logger.error(f"Missing data in the response: {str(key_err)}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to check host resources: {str(e)}")
            raise
