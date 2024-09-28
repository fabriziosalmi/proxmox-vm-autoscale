import logging
import re

class VMResourceManager:
    def __init__(self, ssh_client, vm_id, config):
        self.ssh_client = ssh_client
        self.vm_id = vm_id
        self.config = config
        self.logger = logging.getLogger("vm_resource_manager")

    def get_resource_usage(self):
        """
        Retrieves the CPU and RAM usage of the VM using Proxmox host metrics.
        :return: Tuple of (cpu_usage, ram_usage) as percentages.
        """
        try:
            # Get VM status to confirm if the VM is running
            command = f"qm list | grep {self.vm_id}"
            status_output = self.ssh_client.execute_command(command).strip()
            if "running" not in status_output:
                self.logger.warning(f"VM {self.vm_id} is not running. Skipping resource usage retrieval.")
                return 0.0, 0.0  # Return zero usage if VM is not running

            # Use qm commands to get resource usage from the Proxmox host
            command = f"qm status {self.vm_id} --verbose"
            output = self.ssh_client.execute_command(command)

            # Log the output for debugging purposes
            self.logger.debug(f"Raw output from 'qm status {self.vm_id} --verbose':\n{output}")

            # Parse RAM usage from the output (we could not find CPU usage here, assume zero for now)
            ram_usage = self._parse_ram_usage(output)
            cpu_usage = 0.0  # Assuming no CPU usage data available in the output

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
        command = f"qm config {self.vm_id}"
        output = self.ssh_client.execute_command(command)
        data = re.search(r"cores: (\d+)", output)
        if data:
            return data.group(1)
        else:
            raise ValueError(f"Could not determine CPU cores for VM {self.vm_id}")

    def _set_cpu_cores(self, cores):
        """
        Sets the number of CPU cores for the VM.
        :param cores: Number of CPU cores to set
        """
        command = f"qm set {self.vm_id} -cores {cores}"
        self.ssh_client.execute_command(command)

    def _get_current_ram(self):
        """
        Retrieves the current RAM allocated to the VM in MB.
        :return: Current RAM allocation in MB
        """
        command = f"qm config {self.vm_id}"
        output = self.ssh_client.execute_command(command)
        data = re.search(r"memory: (\d+)", output)
        if data:
            return data.group(1)
        else:
            raise ValueError(f"Could not determine RAM for VM {self.vm_id}")

    def _set_ram(self, ram):
        """
        Sets the RAM for the VM.
        :param ram: RAM value in MB to set
        """
        command = f"qm set {self.vm_id} -memory {ram}"
        self.ssh_client.execute_command(command)

    def _parse_ram_usage(self, output):
        """
        Parses RAM usage information from the command output.
        :param output: Output from the `qm status` command.
        :return: RAM usage as a percentage.
        """
        try:
            # Attempt to calculate memory usage using maxmem and mem
            maxmem_match = re.search(r"maxmem: (\d+)", output)
            mem_match = re.search(r"mem: (\d+)", output)
            if maxmem_match and mem_match:
                maxmem = int(maxmem_match.group(1))
                mem = int(mem_match.group(1))
                return (mem / maxmem) * 100 if maxmem else 0.0
            else:
                self.logger.error(f"Could not parse RAM usage information from output: {output}")
                return 0.0  # Default to 0 if unable to parse
        except Exception as e:
            self.logger.error(f"Error parsing RAM usage: {str(e)}")
            raise

    def _get_max_cores(self):
        return self.config['scaling_limits']['max_cores']

    def _get_min_cores(self):
        return self.config['scaling_limits']['min_cores']

    def _get_max_ram(self):
        return self.config['scaling_limits']['max_ram_mb']

    def _get_min_ram(self):
        return self.config['scaling_limits']['min_ram_mb']
