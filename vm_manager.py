import json
import logging
import re
import time
import threading


class CommandFailed(RuntimeError):
    """A command returned a non-zero exit status on the Proxmox node."""


class VMResourceManager:
    def __init__(self, ssh_client, vm_id, config, vm_config=None):
        self.ssh_client = ssh_client
        self.vm_id = vm_id
        self.config = config
        # The VM's own entry from `virtual_machines`, when the caller has it.
        # Used for per-VM `scaling_limits` overrides.
        self.vm_config = vm_config or {}
        self.logger = logging.getLogger("vm_resource_manager")
        # CPU and RAM are rate-limited independently: a CPU change must not
        # swallow the RAM cooldown (and vice versa) within the same cycle.
        # These keys are also the set of resources can_scale() will accept.
        self._resource_scale_times = {"cpu": 0.0, "ram": 0.0}
        self.scale_cooldown = self.config.get("scale_cooldown", 300)  # Default to 5 minutes
        self.scale_lock = threading.Lock()  # Added lock for scaling control
        self.auto_configure_hotplug = self.config.get("auto_configure_hotplug", True)
        self._hotplug_configured = False
        # When set, no command that changes the hypervisor is issued; the
        # service logs what it would have done instead.
        self.dry_run = bool(self.config.get("dry_run", False))

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
                self._run(f"qm set {self.vm_id} {' '.join(updates)}", mutating=True)
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

    @staticmethod
    def _unpack(result):
        """Normalise an SSH result into (stdout, stderr, exit_status).

        `execute_command` returns a 3-tuple, but doubles in the test suite and
        older call paths hand back a bare string; treat that as a success.
        """
        if isinstance(result, tuple):
            out = result[0] if len(result) > 0 and result[0] is not None else ""
            err = result[1] if len(result) > 1 and result[1] is not None else ""
            status = result[2] if len(result) > 2 and result[2] is not None else 0
            return str(out).strip(), str(err).strip(), int(status)
        return (str(result).strip() if result is not None else ""), "", 0

    def _run(self, command, check=True, mutating=False):
        """Run a command on the node and return its stdout.

        `check` makes a non-zero exit status raise. Without it, `qm set`
        failures were invisible: `execute_command` returns the status rather
        than raising, and every caller discarded it, so the service logged
        "RAM set to 4096 MB" whether or not the command had worked.

        `mutating` marks a command that changes the hypervisor. Those are the
        ones dry-run refuses to execute.
        """
        if mutating and self.dry_run:
            self.logger.info(f"[dry-run] VM {self.vm_id}: would run `{command}`")
            return ""

        result = self.ssh_client.execute_command(command)
        output, error, status = self._unpack(result)

        if check and status != 0:
            detail = f": {error}" if error else ""
            raise CommandFailed(
                f"`{command}` failed on VM {self.vm_id} with exit status {status}{detail}"
            )
        return output

    def is_vm_running(self, retries=3, delay=5):
        """Check if the VM is running with retries and improved error handling."""
        for attempt in range(1, retries + 1):
            try:
                command = f"qm status {self.vm_id} --verbose"
                self.logger.debug(f"Executing command to check VM status: {command}")
                output_str, error, status = self._unpack(
                    self.ssh_client.execute_command(command)
                )
                self.logger.debug(f"Command output: {output_str}")

                if status != 0:
                    # A VM that does not exist is not a transient fault; retrying
                    # three times with backoff would just cost 30 seconds.
                    self.logger.error(
                        f"`{command}` failed with exit status {status}"
                        f"{': ' + error if error else ''}"
                    )
                    return False

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
        """Read this value from `qm config`.

        Raises when the command itself fails, rather than substituting a
        default: a fabricated value would be fed straight into a scaling
        decision. `vcpus` is omitted from `qm config` when every core is online.
        """
        output = self._run(f"qm config {self.vm_id}")
        match = re.search(r"vcpus:\s*(\d+)", output)
        return int(match.group(1)) if match else 1

    def _get_current_cores(self):
        """Read this value from `qm config`.

        Raises when the command itself fails, rather than substituting a
        default: a fabricated value would be fed straight into a scaling
        decision. `cores` is omitted from `qm config` at the Proxmox default of 1.
        """
        output = self._run(f"qm config {self.vm_id}")
        match = re.search(r"cores:\s*(\d+)", output)
        return int(match.group(1)) if match else 1

    def _scaling_limit(self, key, legacy_key, default):
        """Resolve one scaling limit, most specific source first.

        Order: the VM's own `scaling_limits` block, then the global
        `scaling_limits` section, then a flat top-level key (older config
        layout), then `default`. Per-VM limits let a 2-core web server and a
        16-core database share one instance, which global-only limits could
        not express.
        """
        per_vm = self.vm_config.get("scaling_limits") or {}
        if isinstance(per_vm, dict) and per_vm.get(key) is not None:
            return per_vm[key]

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
        """Read this value from `qm config`.

        Raises when the command itself fails, rather than substituting a
        default: a fabricated value would be fed straight into a scaling
        decision. `memory` is omitted from `qm config` at the Proxmox default of 512 MB.
        """
        output = self._run(f"qm config {self.vm_id}")
        match = re.search(r"memory:\s*(\d+)", output)
        return int(match.group(1)) if match else 512

    def _get_max_ram(self):
        """Retrieve maximum allowed RAM in MB."""
        return self._scaling_limit("max_ram_mb", "max_ram", 16384)

    def _get_min_ram(self):
        """Retrieve minimum allowed RAM in MB."""
        return self._scaling_limit("min_ram_mb", "min_ram", 512)

    def _check_hotplug_enabled(self):
        """Whether CPU and memory hotplug are enabled for this VM.

        Raises when `qm config` fails. Reporting "no hotplug" on a failed read
        would silently downgrade a live scale to a reboot-required one.
        """
        output = self._run(f"qm config {self.vm_id}")
        hotplug_match = re.search(r"hotplug:\s*([^\n]+)", output)
        if not hotplug_match:
            # No hotplug line at all means hotplug is off.
            return False, False
        settings = hotplug_match.group(1).lower()
        return 'cpu' in settings, 'memory' in settings

    def _check_numa_enabled(self):
        """Whether NUMA is enabled on this VM (required for memory hotplug)."""
        output = self._run(f"qm config {self.vm_id}")
        numa_match = re.search(r"numa:\s*(\d+)", output)
        return bool(numa_match) and int(numa_match.group(1)) == 1

    def _get_balloon_value(self):
        """Current balloon target in MB, or None when the key is absent."""
        output = self._run(f"qm config {self.vm_id}")
        match = re.search(r"balloon:\s*(\d+)", output)
        return int(match.group(1)) if match else None

    def _set_ram(self, ram):
        """Set the RAM for the VM, using balloon for hotplug if available."""
        try:
            is_running = self.is_vm_running()
            _, memory_hotplug = self._check_hotplug_enabled()
            numa_enabled = self._check_numa_enabled()
            
            if is_running and memory_hotplug and numa_enabled:
                # Use balloon for immediate effect on running VMs with hotplug
                self._run(f"qm set {self.vm_id} -balloon {ram}", mutating=True)
                self.logger.info(f"RAM balloon set to {ram} MB for VM {self.vm_id} (hotplug applied).")
            elif is_running and memory_hotplug and not numa_enabled:
                # Hotplug enabled but NUMA not - this won't work properly
                self.logger.warning(
                    f"VM {self.vm_id} has memory hotplug enabled but NUMA is disabled. "
                    "Memory changes will require a reboot. Enable NUMA for live memory scaling."
                )
                self._run(f"qm set {self.vm_id} -memory {ram}", mutating=True)
                self.logger.info(f"RAM config set to {ram} MB for VM {self.vm_id} (requires reboot).")
            elif is_running:
                # No hotplug - warn and set config only
                self.logger.warning(
                    f"VM {self.vm_id} does not have memory hotplug enabled. "
                    "Memory changes will require a reboot. Enable 'hotplug: memory' and NUMA for live scaling."
                )
                self._run(f"qm set {self.vm_id} -memory {ram}", mutating=True)
                self.logger.info(f"RAM config set to {ram} MB for VM {self.vm_id} (requires reboot).")
            else:
                # VM not running - just set memory config
                self._run(f"qm set {self.vm_id} -memory {ram}", mutating=True)
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
            self._run(f"qm set {self.vm_id} -cores {cores}", mutating=True)
            self.logger.debug(f"CPU cores config set to {cores} for VM {self.vm_id}.")
        except Exception as e:
            self.logger.error(f"Failed to set CPU cores to {cores}: {e}")
            raise

    def _set_vcpus(self, vcpus):
        """Set the vCPUs for the VM (can be hotplugged if enabled)."""
        try:
            self._run(f"qm set {self.vm_id} -vcpus {vcpus}", mutating=True)
            self.logger.debug(f"vCPUs set to {vcpus} for VM {self.vm_id}.")
        except Exception as e:
            self.logger.error(f"Failed to set vCPUs to {vcpus}: {e}")
            raise