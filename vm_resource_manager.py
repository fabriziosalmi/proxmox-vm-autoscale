import logging
import subprocess

class VMResourceManager:
    def __init__(self, ssh_client, vm_id):
        self.ssh_client = ssh_client
        self.vm_id = vm_id
        self.logger = logging.getLogger("vm_resource_manager")

    def get_resource_usage(self):
        """
        Retrieves the CPU and RAM usage of the VM.
        :return: Tuple of (cpu_usage, ram_usage) as percentages
        """
        try:
            # Run `pct status` command to get VM resource usage data
            command = f"pct status {self.vm_id} --output-format json"
            output = self.ssh_client.execute_command(command)
            
            # Parse JSON output
            data = json.loads(output)
            cpu_usage = data['cpu'] * 100   # Convert to percentage
            ram_usage = (data['mem'] / data['maxmem']) * 100  # Convert to percentage
            
            return cpu_usage, ram_usage
        except Exception as e:
            self.logger.error(f"Failed to get resource usage for VM {self.vm_id}: {str(e)}")
            raise

    def scale_cpu(self, direction):
        """
        Scales the CPU for the VM up or down based on the given direction.
        :param direction: 'up' to increase CPU, 'down' to decrease CPU
        """
        try:
            current_cores = int(self._get_current_cpu_cores())
            if direction == 'up':
                new_cores = min(current_cores + 1, self._get_max_cores())
                if new_cores > current_cores:
                    self._set_cpu_cores(new_cores)
                    self.logger.info(f"VM {self.vm_id} CPU scaled up to {new_cores} cores")
            elif direction == 'down':
                new_cores = max(current_cores - 1, self._get_min_cores())
                if new_cores < current_cores:
                    self._set_cpu_cores(new_cores)
                    self.logger.info(f"VM {self.vm_id} CPU scaled down to {new_cores} cores")
        except Exception as e:
            self.logger.error(f"Failed to scale CPU for VM {self.vm_id}: {str(e)}")
            raise

    def scale_ram(self, direction):
        """
        Scales the RAM for the VM up or down based on the given direction.
        :param direction: 'up' to increase RAM, 'down' to decrease RAM
        """
        try:
            current_ram = int(self._get_current_ram())
            if direction == 'up':
                new_ram = min(current_ram + 512, self._get_max_ram())
                if new_ram > current_ram:
                    self._set_ram(new_ram)
                    self.logger.info(f"VM {self.vm_id} RAM scaled up to {new_ram} MB")
            elif direction == 'down':
                new_ram = max(current_ram - 512, self._get_min_ram())
                if new_ram < current_ram:
                    self._set_ram(new_ram)
                    self.logger.info(f"VM {self.vm_id} RAM scaled down to {new_ram} MB")
        except Exception as e:
            self.logger.error(f"Failed to scale RAM for VM {self.vm_id}: {str(e)}")
            raise

    def _get_current_cpu_cores(self):
        """
        Retrieves the current number of CPU cores allocated to the VM.
        :return: Current CPU core count
        """
        command = f"pct config {self.vm_id} --output-format json"
        output = self.ssh_client.execute_command(command)
        data = json.loads(output)
        return data.get('cores', 1)

    def _set_cpu_cores(self, cores):
        """
        Sets the number of CPU cores for the VM.
        :param cores: Number of CPU cores to set
        """
        command = f"pct set {self.vm_id} -cores {cores}"
        self.ssh_client.execute_command(command)

    def _get_current_ram(self):
        """
        Retrieves the current RAM allocated to the VM in MB.
        :return: Current RAM allocation in MB
        """
        command = f"pct config {self.vm_id} --output-format json"
        output = self.ssh_client.execute_command(command)
        data = json.loads(output)
        return data.get('memory', 512)

    def _set_ram(self, ram):
        """
        Sets the RAM for the VM.
        :param ram: RAM value in MB to set
        """
        command = f"pct set {self.vm_id} -memory {ram}"
        self.ssh_client.execute_command(command)

    def _get_max_cores(self):
        """
        Retrieves the maximum number of CPU cores allowed for scaling.
        :return: Maximum CPU core count
        """
        return config['scaling_limits']['max_cores']

    def _get_min_cores(self):
        """
        Retrieves the minimum number of CPU cores allowed for scaling.
        :return: Minimum CPU core count
        """
        return config['scaling_limits']['min_cores']

    def _get_max_ram(self):
        """
        Retrieves the maximum amount of RAM allowed for scaling in MB.
        :return: Maximum RAM in MB
        """
        return config['scaling_limits']['max_ram_mb']

    def _get_min_ram(self):
        """
        Retrieves the minimum amount of RAM allowed for scaling in MB.
        :return: Minimum RAM in MB
        """
        return config['scaling_limits']['min_ram_mb']
