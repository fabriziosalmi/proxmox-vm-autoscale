import logging
import re

class VMResourceManager:
    def __init__(self, ssh_client, vm_id, config):
        self.ssh_client = ssh_client
        self.vm_id = vm_id
        self.config = config
        self.logger = logging.getLogger("vm_resource_manager")

    def is_vm_running(self):
        """
        Check if the VM is running.
        :return: True if the VM is running, False otherwise.
        """
        try:
            command = f"qm status {self.vm_id} --verbose"
            output = self.ssh_client.execute_command(command)
            if "status: running" in output:
                return True
            else:
                self.logger.info(f"VM {self.vm_id} is not running. Skipping scaling operations.")
                return False
        except Exception as e:
            self.logger.error(f"Failed to get status for VM {self.vm_id}: {str(e)}")
            return False

    def get_resource_usage(self):
        """
        Retrieves the CPU and RAM usage of the VM using Proxmox host metrics.
        :return: Tuple of (cpu_usage, ram_usage) as percentages.
        """
        try:
            if not self.is_vm_running():
                return 0.0, 0.0  # Return zero usage if VM is not running

            # Get the current status of the VM
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
        Scales the virtual CPUs (vcpus) for the VM up or down based on the given direction.
        :param direction: 'up' to increase vcpus, 'down' to decrease vcpus
        """
        try:
            # Set the maximum cores based on configuration (fixed, not changed dynamically)
            max_cores = self._get_max_cores()
            self._set_max_cores(max_cores)

            # Get the current vcpus allocated to the VM
            current_vcpus = int(self._get_current_vcpus())

            # Scale vcpus based on the given direction
            if direction == 'up':
                new_vcpus = min(current_vcpus + 1, max_cores)
                if new_vcpus > current_vcpus:
                    self._set_vcpus(new_vcpus)
                    self.logger.info(f"VM {self.vm_id} vCPUs scaled up to {new_vcpus}")
            elif direction == 'down':
                new_vcpus = max(current_vcpus - 1, self._get_min_vcpus())
                if new_vcpus < current_vcpus:
                    self._set_vcpus(new_vcpus)
                    self.logger.info(f"VM {self.vm_id} vCPUs scaled down to {new_vcpus}")

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

    def _get_current_vcpus(self):
        """
        Retrieves the current number of vCPUs allocated to the VM.
        :return: Current vCPU count
        """
        command = f"qm config {self.vm_id}"
        output = self.ssh_client.execute_command(command)
        data = re.search(r"vcpus: (\d+)", output)
        if data:
            return data.group(1)
        else:
            raise ValueError(f"Could not determine vCPUs for VM {self.vm_id}")

    def _set_vcpus(self, vcpus):
        """
        Sets the number of vCPUs for the VM.
        :param vcpus: Number of vCPUs to set
        """
        command = f"qm set {self.vm_id} -vcpus {vcpus}"
        self.ssh_client.execute_command(command)

    def _get_max_cores(self):
        return self.config['scaling_limits']['max_cores']

    def _get_min_vcpus(self):
        return self.config['scaling_limits']['min_cores']

    def _set_max_cores(self, cores):
        """
        Sets the maximum number of CPU cores for the VM. This is not changed dynamically.
        :param cores: Maximum number of CPU cores
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
