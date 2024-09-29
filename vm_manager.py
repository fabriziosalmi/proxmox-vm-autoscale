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
    
            # Get the current vcpus and cores allocated to the VM
            current_cores = int(self._get_current_cores())
            current_vcpus = self._get_current_vcpus()
    
            # Adjust cores to ensure we do not exceed the maximum allowed cores
            if current_cores > max_cores:
                self.logger.info(f"Adjusting 'cores' down to max limit: {max_cores}")
                self._set_max_cores(max_cores)
                current_cores = max_cores  # Update current cores
    
            # Scaling up
            if direction == 'up':
                # Ensure cores are adjusted before increasing vcpus
                if current_cores < max_cores:
                    self.logger.info(f"Scaling up cores from {current_cores} to {current_cores + 1}")
                    self._set_max_cores(current_cores + 1)
                    current_cores += 1  # Update current cores
    
                # Increase vcpus, but ensure they don't exceed the current number of cores
                new_vcpus = min(current_vcpus + 1, current_cores)
                if new_vcpus > current_vcpus:
                    self.logger.info(f"Scaling up vCPUs from {current_vcpus} to {new_vcpus}")
                    self._set_vcpus(new_vcpus)
                    self.logger.info(f"VM {self.vm_id} vCPUs scaled up to {new_vcpus}")
    
            # Scaling down
            elif direction == 'down':
                # Decrease vcpus, ensure it's at least 1 and does not exceed the current cores
                new_vcpus = max(current_vcpus - 1, 1)
    
                # If vcpus is greater than cores, adjust cores first
                if new_vcpus > current_cores:
                    self.logger.info(f"Adjusting 'cores' to match the required 'vcpus' value: {new_vcpus}")
                    self._set_max_cores(new_vcpus)
                    current_cores = new_vcpus  # Update current cores
    
                # Now set the new vcpus value, ensuring it is within bounds
                if new_vcpus <= current_cores and new_vcpus < current_vcpus:
                    self.logger.info(f"Scaling down vCPUs from {current_vcpus} to {new_vcpus}")
                    self._set_vcpus(new_vcpus)
                    self.logger.info(f"VM {self.vm_id} vCPUs scaled down to {new_vcpus}")
    
        except Exception as e:
            self.logger.error(f"Failed to scale CPU for VM {self.vm_id}: {str(e)}")
            raise



    def _get_current_vcpus(self):
        """
        Retrieves the current number of vCPUs allocated to the VM.
        :return: Current vCPU count
        """
        command = f"qm config {self.vm_id}"
        output = self.ssh_client.execute_command(command)

        # Log output for debugging
        self.logger.debug(f"Raw output from 'qm config {self.vm_id}':\n{output}")

        # Try to find the vcpus setting first
        data = re.search(r"vcpus: (\d+)", output)
        if data:
            return int(data.group(1))

        # Fallback to using cores if vcpus is not explicitly defined
        self.logger.warning(f"'vcpus' not found for VM {self.vm_id}. Falling back to 'cores' value.")
        return int(self._get_current_cores())

    def _get_current_cores(self):
        """
        Retrieves the current number of CPU cores allocated to the VM.
        :return: Current core count
        """
        command = f"qm config {self.vm_id}"
        output = self.ssh_client.execute_command(command)

        # Log output for debugging
        self.logger.debug(f"Raw output from 'qm config {self.vm_id}':\n{output}")

        data = re.search(r"cores: (\d+)", output)
        if data:
            return int(data.group(1))
        else:
            raise ValueError(f"Could not determine CPU cores for VM {self.vm_id}")

    def _set_vcpus(self, vcpus):
        """
        Sets the number of vCPUs for the VM.
        :param vcpus: Number of vCPUs to set
        """
        command = f"qm set {self.vm_id} -vcpus {vcpus}"
        self.ssh_client.execute_command(command)

    def _set_max_cores(self, cores):
        """
        Sets the maximum number of CPU cores for the VM.
        :param cores: Maximum number of CPU cores
        """
        command = f"qm set {self.vm_id} -cores {cores}"
        self.ssh_client.execute_command(command)

    def _get_max_cores(self):
        """
        Retrieves the maximum number of CPU cores allowed for scaling.
        :return: Max core count from the configuration.
        """
        return self.config['scaling_limits']['max_cores']

    def _get_min_cores(self):
        """
        Retrieves the minimum number of CPU cores allowed for scaling.
        :return: Min core count from the configuration.
        """
        return self.config['scaling_limits']['min_cores']

    def _get_max_ram(self):
        """
        Retrieves the maximum RAM allowed for scaling (in MB).
        :return: Max RAM in MB from the configuration.
        """
        return self.config['scaling_limits']['max_ram_mb']

    def _get_min_ram(self):
        """
        Retrieves the minimum RAM allowed for scaling (in MB).
        :return: Min RAM in MB from the configuration.
        """
        return self.config['scaling_limits']['min_ram_mb']

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
