import json
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
        # CPU and RAM are rate-limited independently: a CPU change must not
        # swallow the RAM cooldown (and vice versa) within the same cycle.
        # These keys are also the set of resources can_scale() will accept.
        self._resource_scale_times = {"cpu": 0.0, "ram": 0.0}
        self.scale_cooldown = self.config.get("scale_cooldown", 300)  # Default to 5 minutes
        self.scale_lock = threading.Lock()  # Added lock for scaling control
        self.auto_configure_hotplug = self.config.get("auto_configure_hotplug", True)
        self._hotplug_configured = False

        self.ensure_hotplug_configured()

    def ensure_hotplug_configured(self):
        """Ensure hotplug and NUMA are enabled for this VM (for live scaling support).

        Idempotent, and retried until it succeeds: it returns immediately once a
        run has completed without error, but a transient SSH failure leaves the
        flag unset so the next polling cycle tries again. Managers are cached
        across cycles, so without the retry a single bad first cycle would
        disable live scaling for that VM for the lifetime of the process.
        """
        if self._hotplug_configured or not self.auto_configure_hotplug:
            return

        try:
            cpu_hotplug, memory_hotplug = self._check_hotplug_enabled()
            numa_enabled = self._check_numa_enabled()
            
            needs_update = False
            updates = []
            
            # Check if we need to enable hotplug for CPU/memory
            if not cpu_hotplug or not memory_hotplug:
                updates.append("-hotplug cpu,memory,network,disk,usb")
                needs_update = True
                self.logger.info(f"VM {self.vm_id}: Enabling hotplug for cpu,memory,network,disk,usb")
            
            # Check if we need to enable NUMA (required for memory hotplug)
            if not numa_enabled:
                updates.append("-numa 1")
                needs_update = True
                self.logger.info(f"VM {self.vm_id}: Enabling NUMA for memory hotplug support")
            
            if needs_update:
                command = f"qm set {self.vm_id} {' '.join(updates)}"
                output = self.ssh_client.execute_command(command)
                self._get_command_output(output)
                self.logger.info(
                    f"VM {self.vm_id}: Hotplug configuration updated. "
                    "Note: NUMA changes require a VM restart to take effect."
                )

            self._hotplug_configured = True
        except Exception as e:
            # Leave _hotplug_configured False so the next cycle retries.
            self.logger.warning(
                f"Failed to auto-configure hotplug for VM {self.vm_id}: {e}. "
                "Will retry on the next cycle."
            )

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
        """Return (cpu_percent, ram_percent) for this VM.

        Either element is ``None`` when that metric could not be read. ``None``
        is deliberately distinct from ``0.0``: an unreadable metric used to be
        reported as zero, which sits below every sensible ``low`` threshold, so
        a parsing failure was indistinguishable from an idle guest and walked
        the VM down to its minimum one step per cycle. Callers must skip
        scaling on ``None`` rather than acting on it.

        A guest that is powered off genuinely uses nothing, so that case still
        reports ``(0.0, 0.0)``.
        """
        try:
            if not self.is_vm_running():
                return 0.0, 0.0

            resource = self._fetch_cluster_resource()
            if resource is None:
                return None, None

            cpu_usage = self._cpu_percent(resource)
            ram_usage = self._ram_percent(resource)
            return cpu_usage, ram_usage
        except Exception as e:
            self.logger.error(f"Failed to retrieve resource usage for VM {self.vm_id}: {e}")
            return None, None

    def _fetch_cluster_resource(self):
        """Return this VM's entry from `pvesh get /cluster/resources`, or None.

        Asks for JSON rather than scraping the human-readable table. The old
        `grep 'qemu/<vmid>' | awk -F '│'` pipeline depended on box-drawing
        separators and fixed column positions, both of which move between
        Proxmox versions, and its substring match also caught VMID 1010 when
        looking for 101.
        """
        command = "pvesh get /cluster/resources --output-format json"
        output = self.ssh_client.execute_command(command)
        output_str = self._get_command_output(output)

        if not output_str:
            self.logger.warning(
                f"VM {self.vm_id}: empty response from `{command}`."
            )
            return None

        try:
            resources = json.loads(output_str)
        except ValueError as e:
            self.logger.error(
                f"VM {self.vm_id}: could not parse cluster resources as JSON: {e}"
            )
            return None

        if not isinstance(resources, list):
            self.logger.error(
                f"VM {self.vm_id}: unexpected cluster resources payload "
                f"({type(resources).__name__}, expected a list)."
            )
            return None

        wanted = str(self.vm_id)
        for entry in resources:
            if not isinstance(entry, dict) or entry.get("type") != "qemu":
                continue
            if str(entry.get("vmid")) == wanted:
                return entry

        self.logger.warning(
            f"VM {self.vm_id}: not present in cluster resources. "
            "It may have been removed, or it belongs to another cluster."
        )
        return None

    def _cpu_percent(self, resource):
        """CPU usage as a percentage, or None when the field is unusable.

        Proxmox reports `cpu` as a fraction of the guest's allocated CPUs.
        """
        value = resource.get("cpu")
        if value is None:
            self.logger.warning(f"VM {self.vm_id}: no 'cpu' field in cluster resources.")
            return None
        try:
            return float(value) * 100
        except (TypeError, ValueError):
            self.logger.warning(f"VM {self.vm_id}: non-numeric 'cpu' value {value!r}.")
            return None

    def _ram_percent(self, resource):
        """RAM usage as a percentage of the guest's maximum, or None.

        `mem` and `maxmem` are byte counts.
        """
        used = resource.get("mem")
        total = resource.get("maxmem")
        if used is None or total is None:
            self.logger.warning(
                f"VM {self.vm_id}: missing 'mem' or 'maxmem' in cluster resources."
            )
            return None
        try:
            used = float(used)
            total = float(total)
        except (TypeError, ValueError):
            self.logger.warning(
                f"VM {self.vm_id}: non-numeric memory values "
                f"mem={used!r} maxmem={total!r}."
            )
            return None

        if total <= 0:
            self.logger.warning(
                f"VM {self.vm_id}: maxmem is {total}; cannot compute a usage percentage."
            )
            return None

        return (used / total) * 100

    def _validate_resource(self, resource):
        """Reject unknown resource names instead of silently skipping the cooldown."""
        if resource not in self._resource_scale_times:
            raise ValueError(
                f"Unknown scalable resource {resource!r}; "
                f"expected one of {sorted(self._resource_scale_times)}"
            )

    def can_scale(self, resource="cpu"):
        """Report whether `resource` ("cpu" or "ram") is out of its cooldown.

        This is a read-only check: the cooldown is consumed by `_mark_scaled`,
        which is called only after a scaling command has actually been issued.
        A threshold breach that results in no change therefore does not block
        the next check.

        Cooldowns are strictly per resource; there is deliberately no global
        timer, because sharing one is what let a CPU change suppress RAM
        scaling for the rest of the cycle.
        """
        self._validate_resource(resource)
        with self.scale_lock:
            elapsed = time.time() - self._resource_scale_times[resource]
            return elapsed >= self.scale_cooldown

    def _mark_scaled(self, resource):
        """Start the cooldown for `resource` after a successful scaling action."""
        self._validate_resource(resource)
        with self.scale_lock:
            self._resource_scale_times[resource] = time.time()

    def scale_cpu(self, direction):
        """Scale the CPU cores and vCPUs of the VM."""
        if not self.can_scale("cpu"):
            return False

        try:
            current_cores = self._get_current_cores()
            max_cores = self._get_max_cores()
            min_cores = self._get_min_cores()
            current_vcpus = self._get_current_vcpus()

            if direction == "up" and current_cores < max_cores:
                self._scale_cpu_up(current_cores, current_vcpus)
                self._mark_scaled("cpu")
                return True
            elif direction == "down" and current_cores > min_cores:
                self._scale_cpu_down(current_cores, current_vcpus)
                self._mark_scaled("cpu")
                return True
            else:
                self.logger.info("No CPU scaling required.")
                return False
        except Exception as e:
            self.logger.error(f"Failed to scale CPU: {e}")
            raise

    def scale_ram(self, direction):
        """Scale the RAM of the VM."""
        if not self.can_scale("ram"):
            return False

        try:
            current_ram = self._get_current_ram()
            max_ram = self._get_max_ram()
            min_ram = self._get_min_ram()

            if direction == "up" and current_ram < max_ram:
                new_ram = min(current_ram + 512, max_ram)
                self._set_ram(new_ram)
                self._mark_scaled("ram")
                return True
            elif direction == "down" and current_ram > min_ram:
                new_ram = max(current_ram - 512, min_ram)
                self._set_ram(new_ram)
                self._mark_scaled("ram")
                return True
            else:
                self.logger.info("No RAM scaling required.")
            return False
        except Exception as e:
            self.logger.error(f"Failed to scale RAM: {e}")
            raise

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

    def _scaling_limit(self, key, legacy_key, default):
        """Read a limit from the `scaling_limits` section of the config.

        Falls back to a flat top-level key (older config layout) and finally to
        `default`, so existing installations keep working after the move to
        the documented `scaling_limits:` section.
        """
        limits = self.config.get("scaling_limits") or {}
        if key in limits and limits[key] is not None:
            return limits[key]
        if legacy_key in self.config and self.config[legacy_key] is not None:
            return self.config[legacy_key]
        return default

    def _get_max_cores(self):
        """Retrieve maximum allowed CPU cores."""
        return self._scaling_limit("max_cores", "max_cores", 8)

    def _get_min_cores(self):
        """Retrieve minimum allowed CPU cores."""
        return self._scaling_limit("min_cores", "min_cores", 1)

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
        """Retrieve maximum allowed RAM in MB."""
        return self._scaling_limit("max_ram_mb", "max_ram", 16384)

    def _get_min_ram(self):
        """Retrieve minimum allowed RAM in MB."""
        return self._scaling_limit("min_ram_mb", "min_ram", 512)

    def _check_hotplug_enabled(self):
        """Check if hotplug is enabled for CPU and memory on this VM."""
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)
            output_str = self._get_command_output(output)
            
            # Check for hotplug setting (e.g., "hotplug: cpu,memory" or "hotplug: network,disk,cpu,memory")
            hotplug_match = re.search(r"hotplug:\s*([^\n]+)", output_str)
            if hotplug_match:
                hotplug_settings = hotplug_match.group(1).lower()
                cpu_hotplug = 'cpu' in hotplug_settings
                memory_hotplug = 'memory' in hotplug_settings
                return cpu_hotplug, memory_hotplug
            
            # If no hotplug line, hotplug is disabled
            return False, False
        except Exception as e:
            self.logger.error(f"Failed to check hotplug settings: {e}")
            return False, False

    def _check_numa_enabled(self):
        """Check if NUMA is enabled on this VM (required for memory hotplug)."""
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)
            output_str = self._get_command_output(output)
            
            # Check for numa setting
            numa_match = re.search(r"numa:\s*(\d+)", output_str)
            if numa_match:
                return int(numa_match.group(1)) == 1
            return False
        except Exception as e:
            self.logger.error(f"Failed to check NUMA settings: {e}")
            return False

    def _get_balloon_value(self):
        """Get current balloon memory value."""
        try:
            command = f"qm config {self.vm_id}"
            output = self.ssh_client.execute_command(command)
            output_str = self._get_command_output(output)
            match = re.search(r"balloon:\s*(\d+)", output_str)
            return int(match.group(1)) if match else None
        except Exception as e:
            self.logger.debug(f"Failed to get balloon value: {e}")
            return None

    def _set_ram(self, ram):
        """Set the RAM for the VM, using balloon for hotplug if available."""
        try:
            is_running = self.is_vm_running()
            _, memory_hotplug = self._check_hotplug_enabled()
            numa_enabled = self._check_numa_enabled()
            
            if is_running and memory_hotplug and numa_enabled:
                # Use balloon for immediate effect on running VMs with hotplug
                command = f"qm set {self.vm_id} -balloon {ram}"
                output = self.ssh_client.execute_command(command)
                self._get_command_output(output)
                self.logger.info(f"RAM balloon set to {ram} MB for VM {self.vm_id} (hotplug applied).")
            elif is_running and memory_hotplug and not numa_enabled:
                # Hotplug enabled but NUMA not - this won't work properly
                self.logger.warning(
                    f"VM {self.vm_id} has memory hotplug enabled but NUMA is disabled. "
                    "Memory changes will require a reboot. Enable NUMA for live memory scaling."
                )
                command = f"qm set {self.vm_id} -memory {ram}"
                output = self.ssh_client.execute_command(command)
                self._get_command_output(output)
                self.logger.info(f"RAM config set to {ram} MB for VM {self.vm_id} (requires reboot).")
            elif is_running:
                # No hotplug - warn and set config only
                self.logger.warning(
                    f"VM {self.vm_id} does not have memory hotplug enabled. "
                    "Memory changes will require a reboot. Enable 'hotplug: memory' and NUMA for live scaling."
                )
                command = f"qm set {self.vm_id} -memory {ram}"
                output = self.ssh_client.execute_command(command)
                self._get_command_output(output)
                self.logger.info(f"RAM config set to {ram} MB for VM {self.vm_id} (requires reboot).")
            else:
                # VM not running - just set memory config
                command = f"qm set {self.vm_id} -memory {ram}"
                output = self.ssh_client.execute_command(command)
                self._get_command_output(output)
                self.logger.info(f"RAM set to {ram} MB for VM {self.vm_id}.")
        except Exception as e:
            self.logger.error(f"Failed to set RAM to {ram}: {e}")
            raise

    def _scale_cpu_up(self, current_cores, current_vcpus):
        """Helper method to scale CPU up, using hotplug when available."""
        is_running = self.is_vm_running()
        cpu_hotplug, _ = self._check_hotplug_enabled()
        
        if is_running and cpu_hotplug:
            # For hotplug: prefer adjusting vcpus within current cores limit
            if current_vcpus < current_cores:
                # We can increase vcpus without changing cores
                new_vcpus = current_vcpus + 1
                self._set_vcpus(new_vcpus)
                self.logger.info(f"Scaled up vCPUs to {new_vcpus} for VM {self.vm_id} (hotplug applied).")
            else:
                # vcpus == cores, need to increase cores (config change) then vcpus
                new_cores = current_cores + 1
                self._set_cores(new_cores)
                new_vcpus = current_vcpus + 1
                self._set_vcpus(new_vcpus)
                self.logger.warning(
                    f"VM {self.vm_id}: Increased cores to {new_cores} (requires reboot for full effect) "
                    f"and vCPUs to {new_vcpus} (hotplug applied)."
                )
        elif is_running:
            # No hotplug - config change only, warn user
            new_cores = current_cores + 1
            self._set_cores(new_cores)
            new_vcpus = min(current_vcpus + 1, new_cores)
            self._set_vcpus(new_vcpus)
            self.logger.warning(
                f"VM {self.vm_id} does not have CPU hotplug enabled. "
                "CPU changes will require a reboot. Enable 'hotplug: cpu' for live CPU scaling."
            )
        else:
            # VM not running - just set config
            new_cores = current_cores + 1
            self._set_cores(new_cores)
            new_vcpus = min(current_vcpus + 1, new_cores)
            self._set_vcpus(new_vcpus)

    def _scale_cpu_down(self, current_cores, current_vcpus):
        """Helper method to scale CPU down, using hotplug when available."""
        is_running = self.is_vm_running()
        cpu_hotplug, _ = self._check_hotplug_enabled()
        
        if is_running and cpu_hotplug:
            # For hotplug: reduce vcpus first (immediate effect)
            new_vcpus = max(current_vcpus - 1, 1)
            self._set_vcpus(new_vcpus)
            self.logger.info(f"Scaled down vCPUs to {new_vcpus} for VM {self.vm_id} (hotplug applied).")
            
            # Optionally reduce cores if vcpus is significantly lower
            # (cores change requires reboot, so we only do it when it makes sense)
            new_cores = current_cores - 1
            if new_cores >= new_vcpus and new_cores >= self._get_min_cores():
                self._set_cores(new_cores)
                self.logger.info(
                    f"Also reduced cores config to {new_cores} for VM {self.vm_id} "
                    "(will take effect after reboot)."
                )
        elif is_running:
            # No hotplug - config change only, warn user
            new_vcpus = max(current_vcpus - 1, 1)
            self._set_vcpus(new_vcpus)
            new_cores = current_cores - 1
            self._set_cores(new_cores)
            self.logger.warning(
                f"VM {self.vm_id} does not have CPU hotplug enabled. "
                "CPU changes will require a reboot. Enable 'hotplug: cpu' for live CPU scaling."
            )
        else:
            # VM not running - just set config
            new_vcpus = max(current_vcpus - 1, 1)
            self._set_vcpus(new_vcpus)
            new_cores = current_cores - 1
            self._set_cores(new_cores)

    def _set_cores(self, cores):
        """Set the CPU cores for the VM (config change, requires reboot for running VMs)."""
        try:
            command = f"qm set {self.vm_id} -cores {cores}"
            output = self.ssh_client.execute_command(command)
            self._get_command_output(output)
            self.logger.debug(f"CPU cores config set to {cores} for VM {self.vm_id}.")
        except Exception as e:
            self.logger.error(f"Failed to set CPU cores to {cores}: {e}")
            raise

    def _set_vcpus(self, vcpus):
        """Set the vCPUs for the VM (can be hotplugged if enabled)."""
        try:
            command = f"qm set {self.vm_id} -vcpus {vcpus}"
            output = self.ssh_client.execute_command(command)
            self._get_command_output(output)
            self.logger.debug(f"vCPUs set to {vcpus} for VM {self.vm_id}.")
        except Exception as e:
            self.logger.error(f"Failed to set vCPUs to {vcpus}: {e}")
            raise