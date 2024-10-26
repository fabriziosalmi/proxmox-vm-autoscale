import logging
import re
import time

class VMResourceManager:
    def __init__(self, ssh_client, vm_id, config):
        self.ssh_client = ssh_client
        self.vm_id = vm_id
        self.config = config
        self.logger = logging.getLogger("vm_resource_manager")
        self.last_scale_time = 0  # Initialize last scale time for cooldown
        self.scale_cooldown = self.config.get('scale_cooldown', 300)  # Default cooldown 5 minutes

    def is_vm_running(self, retries=3, delay=5):
        """
        Check if the VM is running.
        :return: True if the VM is running, False otherwise.
        """
        for attempt in range(retries):
            try:
                command = f"qm status {self.vm_id} --verbose"
                output = self.ssh_client.execute_command(command)
                if "status: running" in output:
                    return True
                else:
                    self.logger.info(f"VM {self.vm_id} is not running. Skipping scaling operations.")
                    return False
            except Exception as e:
                self.logger.error(f"Failed to get status for VM {self.vm_id} (attempt {attempt + 1}): {str(e)}")
                time.sleep(delay)
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
    
            # Parse RAM and CPU usage from the output
            success, cpu_usage = self._parse_cpu_usage(output)
            if not success:
                cpu_usage = 0.0  # Default to 0.0 if parsing fails
            
            ram_usage = self._parse_ram_usage(output)
    
            return cpu_usage, ram_usage
    
        except Exception as e:
            self.logger.error(f"Failed to get resource usage for VM {self.vm_id}: {str(e)}")
            raise


    def can_scale(self):
        """
        Determines if scaling operations can be performed based on cooldown and host resource availability.
        :return: True if scaling is allowed, False otherwise.
        """
        current_time = time.time()
        if (current_time - self.last_scale_time) < self.scale_cooldown:
            self.logger.info(f"Scaling operations are on cooldown. Next scaling allowed after {int(self.scale_cooldown - (current_time - self.last_scale_time))} seconds.")
            return False

        # Additional host resource checks can be integrated here if necessary
        return True

    def scale_cpu(self, direction):
        """
        Scales the virtual CPUs (vcpus) for the VM up or down based on the given direction.
        :param direction: 'up' to increase vcpus, 'down' to decrease vcpus
        """
        if not self.can_scale():
            return

        try:
            max_cores = self._get_max_cores()
            min_cores = self._get_min_cores()
            current_cores = int(self._get_current_cores())
            current_vcpus = self._get_current_vcpus()

            # Log the scaling decision
            self.logger.info(f"Current Cores: {current_cores}, Current vCPUs: {current_vcpus}, Max Cores: {max_cores}, Min Cores: {min_cores}")

            # Scaling up
            if direction == 'up' and current_cores < max_cores:
                new_cores = current_cores + 1
                self.logger.info(f"Scaling up cores from {current_cores} to {new_cores}")
                self._set_max_cores(new_cores)

                new_vcpus = min(current_vcpus + 1, new_cores)
                if new_vcpus > current_vcpus:
                    self.logger.info(f"Scaling up vCPUs from {current_vcpus} to {new_vcpus}")
                    self._set_vcpus(new_vcpus)

            # Scaling down
            elif direction == 'down' and current_cores > min_cores:
                new_vcpus = max(current_vcpus - 1, 1)
                if new_vcpus < current_vcpus:
                    self.logger.info(f"Scaling down vCPUs from {current_vcpus} to {new_vcpus}")
                    self._set_vcpus(new_vcpus)

                new_cores = current_cores - 1
                self.logger.info(f"Scaling down cores from {current_cores} to {new_cores}")
                self._set_max_cores(new_cores)

            else:
                self.logger.info(f"No scaling action required for CPU direction '{direction}'.")

            # Update last scale time after successful scaling
            self.last_scale_time = time.time()

        except Exception as e:
            self.logger.error(f"Failed to scale CPU for VM {self.vm_id}: {str(e)}")
            # Optionally implement rollback or alerting here
            raise

    def scale_ram(self, direction):
        """
        Scales the RAM for the VM up or down based on the given direction.
        :param direction: 'up' to increase RAM, 'down' to decrease RAM
        """
        if not self.can_scale():
            return

        try:
            current_ram = int(self._get_current_ram())

            if not self._is_memory_hotplug_enabled():
                self.logger.error(f"Memory hotplug is not enabled for VM {self.vm_id}. Skipping RAM scaling.")
                return

            if direction == 'up':
                new_ram = min(current_ram + 512, self._get_max_ram())
                if new_ram > current_ram:
                    self.logger.info(f"Scaling up RAM from {current_ram} MB to {new_ram} MB")
                    if self._try_set_ram(new_ram):
                        self.logger.info(f"VM {self.vm_id} RAM scaled up to {new_ram} MB")
                    else:
                        self.logger.error(f"Failed to scale up RAM for VM {self.vm_id}")
                        return

            elif direction == 'down':
                new_ram = max(current_ram - 512, self._get_min_ram())
                if new_ram < current_ram:
                    self.logger.info(f"Scaling down RAM from {current_ram} MB to {new_ram} MB")
                    if self._try_set_ram(new_ram):
                        self.logger.info(f"VM {self.vm_id} RAM scaled down to {new_ram} MB")
                    else:
                        self.logger.error(f"Failed to scale down RAM for VM {self.vm_id}")
                        return

            else:
                self.logger.warning(f"Unknown scaling direction '{direction}' for RAM.")
                return

            # Update last scale time after successful scaling
            self.last_scale_time = time.time()

        except Exception as e:
            self.logger.error(f"Failed to scale RAM for VM {self.vm_id}: {str(e)}")
            # Optionally implement rollback or alerting here
            raise

    def _try_set_ram(self, ram):
        """
        Tries to set the RAM for the VM and handles hotplug issues with retries.
        :param ram: RAM value in MB to set
        :return: True if successful, False otherwise
        """
        retries = 3
        delay = 10  # seconds
        for attempt in range(1, retries + 1):
            try:
                command = f"qm set {self.vm_id} -memory {ram}"
                self.logger.debug(f"Executing command to set RAM: {command}")
                self.ssh_client.execute_command(command)
                self.logger.info(f"Successfully set RAM to {ram} MB for VM {self.vm_id}")
                return True
            except Exception as e:
                self.logger.error(f"Attempt {attempt}: Failed to set RAM for VM {self.vm_id}: {str(e)}")
                if attempt < retries:
                    self.logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    self.logger.error(f"All attempts to set RAM for VM {self.vm_id} have failed.")
        return False

    def _is_memory_hotplug_enabled(self):
        """
        Checks if the memory hotplug feature is enabled for the VM.
        :return: True if memory hotplug is enabled, False otherwise
        """
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)

            # Log the output for debugging purposes
            self.logger.debug(f"VM {self.vm_id} config output: {output}")

            # Check if 'memory' is part of the hotplug configuration
            hotplug_match = re.search(r"hotplug:\s*(.*)", output)
            if hotplug_match:
                hotplug_options = hotplug_match.group(1).split(',')
                if 'memory' in hotplug_options:
                    self.logger.info(f"Memory hotplug is enabled for VM {self.vm_id}")
                    return True
                else:
                    self.logger.warning(f"Memory hotplug is NOT enabled for VM {self.vm_id}")
            return False

        except Exception as e:
            self.logger.error(f"Failed to check hotplug status for VM {self.vm_id}: {str(e)}")
            return False

    def _parse_cpu_usage(self, output):
        """
        Parses CPU usage information from the command output.
        Attempts multiple parsing strategies if the initial one fails.
        
        :param output: Output from the `qm status` command.
        :return: A tuple containing a success flag and CPU usage as a percentage.
        """
        # Attempt to parse CPU usage with multiple methods
        parsing_methods = [
            self._parse_cpu_usage_method_1,
            self._parse_cpu_usage_method_2,
            self._parse_cpu_usage_method_3
        ]

        for method in parsing_methods:
            success, cpu_usage = method(output)
            if success:
                return (True, cpu_usage)  # Return on first successful parsing
        
        # If all methods fail, log the output and return failure
        self.logger.warning(f"All parsing methods failed for output: {output}")
        return (False, 0.0)  # Indicate failure with a default value

    def _parse_cpu_usage_method_1(self, output):
        """
        First parsing method for CPU usage.
        :return: A tuple containing a success flag and CPU usage as a percentage.
        """
        try:
            cpu_match = re.search(r"cpu:\s*(\d+\.?\d*)%", output)
            if cpu_match:
                cpu_usage = float(cpu_match.group(1))
                if not (0 <= cpu_usage <= 100):
                    self.logger.warning(f"Invalid CPU usage detected: {cpu_usage}%.")
                    return (False, 0.0)
                self.logger.debug(f"Method 1 - Parsed CPU usage: {cpu_usage}%")
                return (True, cpu_usage)
        except Exception as e:
            self.logger.error(f"Method 1 - Error parsing CPU usage: {str(e)}")
        return (False, 0.0)

    def _parse_cpu_usage_method_2(self, output):
        """
        Second parsing method for CPU usage.
        :return: A tuple containing a success flag and CPU usage as a percentage.
        """
        try:
            cpu_match = re.search(r"CPU usage:\s*(\d+\.?\d*)%", output)  # Example of different format
            if cpu_match:
                cpu_usage = float(cpu_match.group(1))
                if not (0 <= cpu_usage <= 100):
                    self.logger.warning(f"Invalid CPU usage detected: {cpu_usage}%.")
                    return (False, 0.0)
                self.logger.debug(f"Method 2 - Parsed CPU usage: {cpu_usage}%")
                return (True, cpu_usage)
        except Exception as e:
            self.logger.error(f"Method 2 - Error parsing CPU usage: {str(e)}")
        return (False, 0.0)

    def _parse_cpu_usage_method_3(self, output):
        """
        Third parsing method for CPU usage.
        :return: A tuple containing a success flag and CPU usage as a percentage.
        """
        try:
            cpu_match = re.search(r"CPU:\s*(\d+\.?\d*)", output)  # Example of a third format
            if cpu_match:
                cpu_usage = float(cpu_match.group(1))
                if not (0 <= cpu_usage <= 100):
                    self.logger.warning(f"Invalid CPU usage detected: {cpu_usage}%.")
                    return (False, 0.0)
                self.logger.debug(f"Method 3 - Parsed CPU usage: {cpu_usage}%")
                return (True, cpu_usage)
        except Exception as e:
            self.logger.error(f"Method 3 - Error parsing CPU usage: {str(e)}")
        return (False, 0.0)


    def _parse_ram_usage(self, output):
        """
        Parses RAM usage information from the command output.
        :param output: Output from the `qm status` command.
        :return: RAM usage as a percentage.
        """
        try:
            maxmem_match = re.search(r"maxmem: (\d+)", output)
            mem_match = re.search(r"mem: (\d+)", output)
            if maxmem_match and mem_match:
                maxmem = int(maxmem_match.group(1))
                mem = int(mem_match.group(1))
                ram_usage = (mem / maxmem) * 100 if maxmem else 0.0
                self.logger.debug(f"Parsed RAM usage for VM {self.vm_id}: {ram_usage}%")
                return ram_usage
            else:
                self.logger.error(f"Could not parse RAM usage information from output: {output}")
                return 0.0
        except Exception as e:
            self.logger.error(f"Error parsing RAM usage: {str(e)}")
            raise

    def _get_current_vcpus(self):
        """
        Retrieves the current number of vCPUs allocated to the VM.
        :return: Current vCPU count
        """
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)

            # Log output for debugging
            self.logger.debug(f"Raw output from 'qm config {self.vm_id}':\n{output}")

            # Try to find the vcpus setting first
            data = re.search(r"vcpus: (\d+)", output)
            if data:
                vcpus = int(data.group(1))
                self.logger.debug(f"Current vCPUs for VM {self.vm_id}: {vcpus}")
                return vcpus

            # Fallback to using cores if vcpus is not explicitly defined
            self.logger.warning(f"'vcpus' not found for VM {self.vm_id}. Falling back to 'cores' value.")
            return int(self._get_current_cores())

        except Exception as e:
            self.logger.error(f"Failed to get current vCPUs for VM {self.vm_id}: {str(e)}")
            raise

    def _get_current_cores(self):
        """
        Retrieves the current number of CPU cores allocated to the VM.
        :return: Current core count
        """
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)

            # Log output for debugging
            self.logger.debug(f"Raw output from 'qm config {self.vm_id}':\n{output}")

            data = re.search(r"cores: (\d+)", output)
            if data:
                cores = int(data.group(1))
                self.logger.debug(f"Current CPU cores for VM {self.vm_id}: {cores}")
                return cores
            else:
                raise ValueError(f"Could not determine CPU cores for VM {self.vm_id}")
        except Exception as e:
            self.logger.error(f"Failed to get current cores for VM {self.vm_id}: {str(e)}")
            raise

    def _set_vcpus(self, vcpus):
        """
        Sets the number of vCPUs for the VM.
        :param vcpus: Number of vCPUs to set
        """
        try:
            command = f"qm set {self.vm_id} -vcpus {vcpus}"
            self.logger.debug(f"Executing command to set vCPUs: {command}")
            self.ssh_client.execute_command(command)
            self.logger.info(f"Successfully set vCPUs to {vcpus} for VM {self.vm_id}")
        except Exception as e:
            self.logger.error(f"Failed to set vCPUs for VM {self.vm_id}: {str(e)}")
            raise

    def _set_max_cores(self, cores):
        """
        Sets the maximum number of CPU cores for the VM.
        :param cores: Maximum number of CPU cores
        """
        try:
            command = f"qm set {self.vm_id} -cores {cores}"
            self.logger.debug(f"Executing command to set cores: {command}")
            self.ssh_client.execute_command(command)
            self.logger.info(f"Successfully set cores to {cores} for VM {self.vm_id}")
        except Exception as e:
            self.logger.error(f"Failed to set cores for VM {self.vm_id}: {str(e)}")
            raise

    def _get_max_cores(self):
        """
        Retrieves the maximum number of CPU cores allowed for scaling.
        :return: Max core count from the configuration.
        """
        try:
            max_cores = self.config['scaling_limits']['max_cores']
            self.logger.debug(f"Max cores from config for VM {self.vm_id}: {max_cores}")
            return max_cores
        except KeyError:
            self.logger.error("Missing 'max_cores' in scaling_limits configuration.")
            raise

    def _get_min_cores(self):
        """
        Retrieves the minimum number of CPU cores allowed for scaling.
        :return: Min core count from the configuration.
        """
        try:
            min_cores = self.config['scaling_limits']['min_cores']
            self.logger.debug(f"Min cores from config for VM {self.vm_id}: {min_cores}")
            return min_cores
        except KeyError:
            self.logger.error("Missing 'min_cores' in scaling_limits configuration.")
            raise

    def _get_current_ram(self):
        """
        Retrieves the current RAM allocated to the VM in MB.
        :return: Current RAM allocation in MB
        """
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)
            data = re.search(r"memory: (\d+)", output)
            if data:
                current_ram = int(data.group(1))
                self.logger.debug(f"Current RAM for VM {self.vm_id}: {current_ram} MB")
                return current_ram
            else:
                raise ValueError(f"Could not determine RAM for VM {self.vm_id}")
        except Exception as e:
            self.logger.error(f"Failed to get current RAM for VM {self.vm_id}: {str(e)}")
            raise

    def _get_max_ram(self):
        """
        Retrieves the maximum RAM allowed for scaling (in MB).
        :return: Max RAM in MB from the configuration.
        """
        try:
            max_ram = self.config['scaling_limits']['max_ram_mb']
            self.logger.debug(f"Max RAM from config for VM {self.vm_id}: {max_ram} MB")
            return max_ram
        except KeyError:
            self.logger.error("Missing 'max_ram_mb' in scaling_limits configuration.")
            raise

    def _get_min_ram(self):
        """
        Retrieves the minimum RAM allowed for scaling (in MB).
        :return: Min RAM in MB from the configuration.
        """
        try:
            min_ram = self.config['scaling_limits']['min_ram_mb']
            self.logger.debug(f"Min RAM from config for VM {self.vm_id}: {min_ram} MB")
            return min_ram
        except KeyError:
            self.logger.error("Missing 'min_ram_mb' in scaling_limits configuration.")
            raise

    def _try_set_ram(self, ram):
        """
        Tries to set the RAM for the VM and handles hotplug issues with retries.
        :param ram: RAM value in MB to set
        :return: True if successful, False otherwise
        """
        retries = 3
        delay = 10  # seconds
        for attempt in range(1, retries + 1):
            try:
                command = f"qm set {self.vm_id} -memory {ram}"
                self.logger.debug(f"Executing command to set RAM: {command}")
                self.ssh_client.execute_command(command)
                self.logger.info(f"Successfully set RAM to {ram} MB for VM {self.vm_id}")
                return True
            except Exception as e:
                self.logger.error(f"Attempt {attempt}: Failed to set RAM for VM {self.vm_id}: {str(e)}")
                if attempt < retries:
                    self.logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    self.logger.error(f"All attempts to set RAM for VM {self.vm_id} have failed.")
        return False
