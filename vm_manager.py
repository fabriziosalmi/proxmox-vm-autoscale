import logging
import re
import time
import threading


class VMResourceManager:
    def __init__(self, ssh_client, vm_id, config):
        self.ssh_client = ssh_client
        self.vm_id = vm_id
        self.config = config
        self.logger = logging.getLogger("vm_resource_manager")
        self.last_scale_time = 0
        self.scale_cooldown = self.config.get("scale_cooldown", 300)  # Default to 5 minutes
        self.scale_lock = threading.Lock()  # Added lock for scaling control

    def _get_command_output(self, output):
        """Helper method to properly handle command output that might be a tuple."""
        if isinstance(output, tuple):
            # Assuming the first element contains the stdout
            return str(output[0]).strip() if output and output[0] is not None else ""
        return str(output).strip() if output is not None else ""

    def is_vm_running(self, retries=3, delay=5):
        """Check if the VM is running with retries and improved error handling."""
        for attempt in range(1, retries + 1):
            try:
                command = f"qm status {self.vm_id} --verbose"
                self.logger.debug(f"Executing command to check VM status: {command}")
                output = self.ssh_client.execute_command(command)
                output_str = self._get_command_output(output)
                self.logger.debug(f"Command output: {output_str}")
        
                if "status: running" in output_str.lower():
                    self.logger.info(f"VM {self.vm_id} is running.")
                    return True
                elif "status:" in output_str.lower():
                    self.logger.info(f"VM {self.vm_id} is not running.")
                    return False
                else:
                    self.logger.warning(
                        f"Unexpected output while checking VM status: {output_str}"
                    )
            except Exception as e:
                self.logger.warning(
                    f"Attempt {attempt}/{retries} failed to check VM status: {e}. Retrying..."
                )
                time.sleep(delay * attempt)  # Exponential backoff
        
        self.logger.error(
            f"Unable to determine status of VM {self.vm_id} after {retries} attempts."
        )
        return False

    def get_resource_usage(self):
        """Retrieve CPU and RAM usage as percentages."""
        try:
            if not self.is_vm_running():
                return 0.0, 0.0
            #command = f"qm status {self.vm_id} --verbose"
            # Updated command  - this might well be refinable to simpler and faster.
            vmid = self.vm_id
            command = f"pvesh get /cluster/resources | grep 'qemu/{vmid}' | awk -F '│' '{{print $6, $15, $16}}'"
            output = self.ssh_client.execute_command(command)
            # example output: "  3.17%     5.00 GiB     3.82 GiB "
            self.logger.info(f"VM status output: {output}")
            cpu_usage = self._parse_cpu_usage(output)
            ram_usage = self._parse_ram_usage(output)
            return cpu_usage, ram_usage
        except Exception as e:
            self.logger.error(f"Failed to retrieve resource usage: {e}")
            return 0.0, 0.0

    def can_scale(self):
        """Determine if scaling can occur using a lock to avoid race conditions."""
        with self.scale_lock:
            current_time = time.time()
            if current_time - self.last_scale_time < self.scale_cooldown:
                return False
            self.last_scale_time = current_time
            return True

    def scale_cpu(self, direction):
        """Scale the CPU cores and vCPUs of the VM."""
        if not self.can_scale():
            return False

        try:
            current_cores = self._get_current_cores()
            max_cores = self._get_max_cores()
            min_cores = self._get_min_cores()
            current_vcpus = self._get_current_vcpus()

            self.last_scale_time = time.time()
            if direction == "up" and current_cores < max_cores:
                self._scale_cpu_up(current_cores, current_vcpus)
                return True
            elif direction == "down" and current_cores > min_cores:
                self._scale_cpu_down(current_cores, current_vcpus)
                return True
            else:
                self.logger.info("No CPU scaling required.")
                return False
        except Exception as e:
            self.logger.error(f"Failed to scale CPU: {e}")
            raise

    def scale_ram(self, direction):
        """Scale the RAM of the VM."""
        if not self.can_scale():
            return False

        try:
            current_ram = self._get_current_ram()
            max_ram = self._get_max_ram()
            min_ram = self._get_min_ram()

            self.last_scale_time = time.time()
            if direction == "up" and current_ram < max_ram:
                new_ram = min(current_ram + 512, max_ram)
                self._set_ram(new_ram)
                return True
            elif direction == "down" and current_ram > min_ram:
                new_ram = max(current_ram - 512, min_ram)
                self._set_ram(new_ram)
                return True
            else:
                self.logger.info("No RAM scaling required.")
            return False
        except Exception as e:
            self.logger.error(f"Failed to scale RAM: {e}")
            raise

    def _parse_cpu_usage(self, output):
        """Parse CPU usage from VM status output."""
        try:
            output_str = self._get_command_output(output)
            percentage_cpu_match = re.search(r"^\s*(\d+(?:\.\d+)?)%", output_str)
            if percentage_cpu_match:
                return float(percentage_cpu_match.group(1))
            self.logger.warning("CPU usage not found in output.")
            return 0.0
        except Exception as e:
            self.logger.error(f"Error parsing CPU usage: {e}")
            return 0.0
    
    def _convert_to_gib(self, value, unit):
        """ Converts memory units to GiB. """
        unit = unit.lower()
        if unit == 'gib':
            return value
        elif unit == 'mib':
            return value / 1024  # Convert MiB to GiB
        else:
            self.logger.warning(f"Unknown memory unit '{unit}'. Assuming GiB.")
            return value  # Assume GiB if unit is unknown

    def _parse_ram_usage(self, output):
        """ Parses RAM usage from VM status output. """
        try:
            output_str = self._get_command_output(output)
            self.logger.debug(f"Processing output: '{output_str}'")
            # ----------------------------
            # Extract Memory Values
            # ----------------------------
            # Pattern Explanation:
            # - (\d+(?:\.\d+)?)\s+(GiB|MiB) : Capture first memory value and its unit
            # - \s+                         : Match one or more whitespace characters
            # - (\d+(?:\.\d+)?)\s+(GiB|MiB) : Capture second memory value and its unit
            pattern_memory = r"(\d+(?:\.\d+)?)\s+(GiB|MiB)\s+(\d+(?:\.\d+)?)\s+(GiB|MiB)"
            memory_match = re.search(pattern_memory, output_str)
            if memory_match:
                max_mem_value = float(memory_match.group(1))
                max_mem_unit = memory_match.group(2)
                used_mem_value = float(memory_match.group(3))
                used_mem_unit = memory_match.group(4)

                self.logger.debug(f"Extracted Max Memory: {max_mem_value} {max_mem_unit}")
                self.logger.debug(f"Extracted Used Memory: {used_mem_value} {used_mem_unit}")

                # Convert memory values to GiB
                max_mem_gib = self._convert_to_gib(max_mem_value, max_mem_unit)
                used_mem_gib = self._convert_to_gib(used_mem_value, used_mem_unit)

                self.logger.debug(f"Converted Max Memory: {max_mem_gib} GiB")
                self.logger.debug(f"Converted Used Memory: {used_mem_gib} GiB")

                if max_mem_gib == 0:
                    self.logger.warning("Maximum memory is zero. Cannot compute usage percentage.")
                    return 0.0

                # Calculate RAM usage percentage based on memory values
                usage_percentage = (used_mem_gib / max_mem_gib) * 100
                self.logger.debug(f"Calculated RAM Usage: {usage_percentage:.2f}%")
                return usage_percentage
            else:
                self.logger.warning("RAM memory values not found in output.")
                return 0.0

        except Exception as e:
            self.logger.error(f"Error parsing RAM usage: {e}")
            return 0.0

    def _get_current_vcpus(self):
        """Retrieve current vCPUs assigned to the VM."""
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)
            output_str = self._get_command_output(output)
            match = re.search(r"vcpus:\s*(\d+)", output_str)
            return int(match.group(1)) if match else 1
        except Exception as e:
            self.logger.error(f"Failed to retrieve vCPUs: {e}")
            return 1

    def _get_current_cores(self):
        """Retrieve current CPU cores assigned to the VM."""
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)
            output_str = self._get_command_output(output)
            match = re.search(r"cores:\s*(\d+)", output_str)
            return int(match.group(1)) if match else 1
        except Exception as e:
            self.logger.error(f"Failed to retrieve CPU cores: {e}")
            return 1

    def _get_max_cores(self):
        """Retrieve maximum allowed CPU cores."""
        return self.config.get("max_cores", 8)

    def _get_min_cores(self):
        """Retrieve minimum allowed CPU cores."""
        return self.config.get("min_cores", 1)

    def _get_current_ram(self):
        """Retrieve current RAM assigned to the VM."""
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)
            output_str = self._get_command_output(output)
            match = re.search(r"memory:\s*(\d+)", output_str)
            return int(match.group(1)) if match else 512
        except Exception as e:
            self.logger.error(f"Failed to retrieve current RAM: {e}")
            return 512

    def _get_max_ram(self):
        """Retrieve maximum allowed RAM."""
        return self.config.get("max_ram", 16384)

    def _get_min_ram(self):
        """Retrieve minimum allowed RAM."""
        return self.config.get("min_ram", 512)

    def _set_ram(self, ram):
        """Set the RAM for the VM."""
        try:
            command = f"qm set {self.vm_id} -memory {ram}"
            output = self.ssh_client.execute_command(command)
            self._get_command_output(output)  # Process output to catch any errors
            self.logger.info(f"RAM set to {ram} MB for VM {self.vm_id}.")
        except Exception as e:
            self.logger.error(f"Failed to set RAM to {ram}: {e}")
            raise

    def _scale_cpu_up(self, current_cores, current_vcpus):
        """Helper method to scale CPU up."""
        new_cores = current_cores + 1
        self._set_cores(new_cores)
        new_vcpus = min(current_vcpus + 1, new_cores)
        self._set_vcpus(new_vcpus)

    def _scale_cpu_down(self, current_cores, current_vcpus):
        """Helper method to scale CPU down."""
        new_vcpus = max(current_vcpus - 1, 1)
        self._set_vcpus(new_vcpus)
        new_cores = current_cores - 1
        self._set_cores(new_cores)

    def _set_cores(self, cores):
        """Set the CPU cores for the VM."""
        try:
            command = f"qm set {self.vm_id} -cores {cores}"
            output = self.ssh_client.execute_command(command)
            self._get_command_output(output)  # Process output to catch any errors
            self.logger.info(f"CPU cores set to {cores} for VM {self.vm_id}.")
        except Exception as e:
            self.logger.error(f"Failed to set CPU cores to {cores}: {e}")
            raise

    def _set_vcpus(self, vcpus):
        """Set the vCPUs for the VM."""
        try:
            command = f"qm set {self.vm_id} -vcpus {vcpus}"
            output = self.ssh_client.execute_command(command)
            self._get_command_output(output)  # Process output to catch any errors
            self.logger.info(f"vCPUs set to {vcpus} for VM {self.vm_id}.")
        except Exception as e:
            self.logger.error(f"Failed to set vCPUs to {vcpus}: {e}")
            raise