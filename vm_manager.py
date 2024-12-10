import logging
import re
import time


class VMResourceManager:
    def __init__(self, ssh_client, vm_id, config):
        self.ssh_client = ssh_client
        self.vm_id = vm_id
        self.config = config
        self.logger = logging.getLogger("vm_resource_manager")
        self.last_scale_time = 0
        self.scale_cooldown = self.config.get("scale_cooldown", 300)  # Default to 5 minutes

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
            node = self.config.get('host', 'pve')
            command = textwrap.dedent(f"""\
                VMID={vmid};
                NODE={node};
                pvesh get /nodes/$NODE/qemu/$VMID/status/current >/dev/null 2>&1 && \\
                PID=$(pgrep -f "kvm.*-id $VMID") && [ -n "$PID" ] && \\
                read CPU MEM <<<$(ps -p $PID -o %cpu=,%mem=) && \\
                echo "CPU Usage for VM $VMID: $CPU%, Memory Usage: $MEM%" || \\
                echo "VM $VMID is not running or PID not found"
                """)
            output = self.ssh_client.execute_command(command)
            # example output: "CPU Usage for VM 101: 8.4%, Memory Usage: 19.4%"
            self.logger.debug(f"VM status output: {output}")
            cpu_usage = self._parse_cpu_usage(output)
            ram_usage = self._parse_ram_usage(output)
            return cpu_usage, ram_usage
        except Exception as e:
            self.logger.error(f"Failed to retrieve resource usage: {e}")
            return 0.0, 0.0

    def can_scale(self):
        """Check if scaling operations are allowed based on cooldown."""
        current_time = time.time()
        if current_time - self.last_scale_time < self.scale_cooldown:
            remaining_time = int(self.scale_cooldown - (current_time - self.last_scale_time))
            self.logger.info(f"Scaling on cooldown. Try again in {remaining_time} seconds.")
            return False
        return True

    def scale_cpu(self, direction):
        """Scale the CPU cores and vCPUs of the VM."""
        if not self.can_scale():
            return

        try:
            current_cores = self._get_current_cores()
            max_cores = self._get_max_cores()
            min_cores = self._get_min_cores()
            current_vcpus = self._get_current_vcpus()

            if direction == "up" and current_cores < max_cores:
                self._scale_cpu_up(current_cores, current_vcpus)
            elif direction == "down" and current_cores > min_cores:
                self._scale_cpu_down(current_cores, current_vcpus)
            else:
                self.logger.info("No CPU scaling required.")
            self.last_scale_time = time.time()
        except Exception as e:
            self.logger.error(f"Failed to scale CPU: {e}")
            raise

    def scale_ram(self, direction):
        """Scale the RAM of the VM."""
        if not self.can_scale():
            return

        try:
            current_ram = self._get_current_ram()
            max_ram = self._get_max_ram()
            min_ram = self._get_min_ram()

            if direction == "up" and current_ram < max_ram:
                new_ram = min(current_ram + 512, max_ram)
                self._set_ram(new_ram)
            elif direction == "down" and current_ram > min_ram:
                new_ram = max(current_ram - 512, min_ram)
                self._set_ram(new_ram)
            else:
                self.logger.info("No RAM scaling required.")
            self.last_scale_time = time.time()
        except Exception as e:
            self.logger.error(f"Failed to scale RAM: {e}")
            raise

    def _parse_cpu_usage(self, output):
        """Parse CPU usage from VM status output."""
        try:
            output_str = self._get_command_output(output)
            #match = re.search(r"cpu:\s*(\d+\.?\d*)%", output_str)
            match = re.search(r"CPU Usage for VM \d+: (\d+(?:\.\d+)?)%", output_str)
            if match:
                return float(match.group(1))
            self.logger.warning("CPU usage not found in output.")
            return 0.0
        except Exception as e:
            self.logger.error(f"Error parsing CPU usage: {e}")
            return 0.0
    
    def _parse_ram_usage(self, output):
        """Parse RAM usage from VM status output."""
        try:
            output_str = self._get_command_output(output)
            match  = re.search(r"Memory Usage: (\d+(?:\.\d+)?)%", output_str)
            #max_mem_match = re.search(r"maxmem:\s*(\d+)", output_str)
            #mem_match = re.search(r"mem:\s*(\d+)", output_str)
            #if max_mem_match and mem_match:
            #    max_mem = int(max_mem_match.group(1))
            #    mem = int(mem_match.group(1))
            #    return (mem / max_mem) * 100 if max_mem > 0 else 0.0
            if match:
                return float(match.group(1))
            self.logger.warning("RAM usage not found in output.")
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
