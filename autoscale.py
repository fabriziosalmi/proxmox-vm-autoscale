import yaml
import json
import requests
import smtplib
import logging
import logging.config
import signal
import threading
import time
import re
import sys
from config_schema import ConfigurationInvalid, validate as validate_config
from ssh_utils import DEFAULT_KNOWN_HOSTS, SSHClient
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from vm_manager import VMResourceManager
from host_resource_checker import HostResourceChecker
from billing_tracker import BillingDataError, BillingTracker
from metrics import MetricsServer, build_registry
from version import __version__
from typing import Any

class ConfigurationError(Exception):
    """Custom exception for configuration-related errors."""
    pass

class NotificationManager:
    #: How long an identical message is suppressed after being sent. An
    #: unreachable node produced one priority-9 notification per VM per cycle -
    #: twenty VMs on a five-minute interval is 240 an hour, indefinitely, and
    #: the real alert is somewhere underneath them.
    DEFAULT_DEDUP_WINDOW = 900

    def __init__(self, config: dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.dedup_window = config.get('notification_dedup_seconds',
                                       self.DEFAULT_DEDUP_WINDOW)
        self._last_sent: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}
        self.validate_notification_config()

    def _dedup_key(self, message: str) -> str:
        """Collapse messages that differ only in their measured values.

        "CPU: 91.2%" and "CPU: 93.7%" are the same event for alerting
        purposes; without this, every cycle produces a new unique string.
        """
        # Only measurements are collapsed: a decimal, or a number followed by
        # a percent sign. Blanking every digit would make "Host pve1
        # unreachable" and "Host pve2 unreachable" the same event.
        return re.sub(r"\b\d+\.\d+%?|\b\d+%", "#", message)

    def _should_send(self, message: str) -> bool:
        """Rate-limit identical notifications, and say so when resuming."""
        if self.dedup_window <= 0:
            return True

        key = self._dedup_key(message)
        now = time.monotonic()
        last = self._last_sent.get(key)

        if last is not None and (now - last) < self.dedup_window:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            self.logger.debug(f"Suppressing a repeated notification: {message}")
            return False

        skipped = self._suppressed.pop(key, 0)
        if skipped:
            self.logger.info(
                f"Resuming notifications for a repeated event; {skipped} "
                f"identical message(s) were suppressed in the last "
                f"{self.dedup_window}s."
            )
        self._last_sent[key] = now
        return True

    def validate_notification_config(self) -> None:
        """Validate notification configuration at startup."""
        notification_enabled = False

        if self.config.get('gotify', {}).get('enabled', False):
            notification_enabled = True
            gotify_config = self.config.get('gotify', {})
            if not all([gotify_config.get('server_url'), gotify_config.get('app_token')]):
                raise ConfigurationError("Gotify is enabled but configuration is incomplete")

        if self.config.get('alerts', {}).get('email_enabled', False):
            notification_enabled = True
            alerts_config = self.config.get('alerts', {})
            required_fields = ['smtp_server', 'smtp_user', 'email_recipient']
            missing_fields = [field for field in required_fields if not alerts_config.get(field)]
            if missing_fields:
                raise ConfigurationError(f"Email alerts are enabled but missing configuration: {', '.join(missing_fields)}")

        if not notification_enabled:
            self.logger.warning("No notification method is enabled in configuration")

    def _format_message(self, message: str | tuple | Any) -> str:
        """Format message to ensure it's a string."""
        if isinstance(message, tuple):
            # If it's a tuple, join non-empty parts
            return ' '.join(str(part) for part in message if part)
        elif isinstance(message, str):
            return message
        else:
            return str(message)

    def send_gotify_notification(self, message: str, priority: int | None = None) -> None:
        """Send notification via Gotify with retry logic."""
        try:
            gotify_config = self.config.get('gotify', {})
            server_url = gotify_config['server_url'].rstrip('/')  # Remove trailing slash if present
            app_token = gotify_config['app_token']
            final_priority = priority or gotify_config.get('priority', 5)

            formatted_message = self._format_message(message)

            response = requests.post(
                f"{server_url}/message",
                data={
                    "title": "VM Autoscale Alert",
                    "message": formatted_message,
                    "priority": final_priority
                },
                headers={"Authorization": f"Bearer {app_token}"},
                timeout=10
            )
            response.raise_for_status()
            self.logger.info("Gotify notification sent successfully")
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to send Gotify notification: {e!s}")
            raise

    def send_smtp_notification(self, message: str) -> None:
        """Send notification via email with retry logic."""
        try:
            alerts_config = self.config['alerts']
            smtp_config = {
                'host': alerts_config['smtp_server'],
                'port': alerts_config.get('smtp_port', 587),
                'user': alerts_config['smtp_user'],
                'password': alerts_config['smtp_password'],
                'recipient': alerts_config['email_recipient']
            }

            to_emails = [smtp_config['recipient']] if isinstance(smtp_config['recipient'], str) else smtp_config['recipient']
            if not all(isinstance(email, str) for email in to_emails):
                raise ValueError("Invalid email format in recipients")
            formatted_message = self._format_message(message)
            # Updated regex to capture the VM number
            pattern = r"VM\s+(\d+)"
            result = re.search(pattern, formatted_message)
            vm_id = result.group(1) if result else ""
            msg = MIMEMultipart()
            msg['From'] = smtp_config['user']
            msg['To'] = ", ".join(to_emails)
            msg['Subject'] = f"VM Autoscale Alert for VM {vm_id}"
            msg.attach(MIMEText(formatted_message, 'plain'))

            with smtplib.SMTP(smtp_config['host'], smtp_config['port']) as server:
                server.starttls()
                if smtp_config['password']:
                    server.login(smtp_config['user'], smtp_config['password'])
                server.sendmail(smtp_config['user'], to_emails, msg.as_string())

            self.logger.info("Email notification sent successfully")
        except Exception as e:
            self.logger.error(f"Failed to send email notification: {e!s}")
            raise

    def send_notification(self, message: str | tuple | Any, priority: int | None = None) -> None:
        """Send notification through configured channels."""
        sent = False
        errors = []
        formatted_message = self._format_message(message)
        if self.config.get('dry_run', False):
            formatted_message = f"[DRY RUN] {formatted_message}"

        if not self._should_send(formatted_message):
            return

        if self.config.get('gotify', {}).get('enabled', False):
            try:
                self.send_gotify_notification(formatted_message, priority)
                sent = True
            except Exception as e:
                error_msg = f"Failed to send Gotify notification: {e!s}"
                errors.append(error_msg)
                self.logger.error(error_msg)

        if self.config.get('alerts', {}).get('email_enabled', False):
            try:
                self.send_smtp_notification(formatted_message)
                sent = True
            except Exception as e:
                error_msg = f"Failed to send email notification: {e!s}"
                errors.append(error_msg)
                self.logger.error(error_msg)

        if not sent:
            error_summary = f" Errors: {'; '.join(errors)}" if errors else ""
            self.logger.warning(
                f"Failed to send notification through any channel. Message: {formatted_message}.{error_summary}"
            )

class VMAutoscaler:
    def __init__(self, config_path: str, logging_config_path: str | None = None):
        self.config = self._load_config(config_path)
        self.logger = self._setup_logging(logging_config_path)
        self._report_config_warnings()
        self.notification_manager = NotificationManager(self.config, self.logger)
        # VMResourceManager instances are reused across polling cycles so the
        # scaling cooldown survives between iterations of the main loop, and so
        # hotplug auto-configuration runs once per VM instead of every cycle.
        self._vm_managers: dict[str, VMResourceManager] = {}
        # Last observed running state per VM, so billing records transitions
        # rather than one entry per poll.
        self._vm_states: dict[str, bool] = {}
        # Consecutive observations below the low threshold, per VM and
        # resource. Growing is fail-safe and acts at once; shrinking can drive
        # a guest into swap or refuse a vCPU unplug, so it has to be earned.
        self._low_streaks: dict[tuple[str, str], int] = {}
        self.scale_down_after = int(self.config.get('scale_down_after_cycles', 2))
        self.dry_run = bool(self.config.get('dry_run', False))
        self.metrics = build_registry()
        self.metrics.set('vm_autoscale_build_info', 1,
                         {'version': __version__,
                          'dry_run': str(self.dry_run).lower()})
        self._metrics_server = self._start_metrics_server()
        if self.dry_run:
            self.logger.warning(
                "DRY RUN: no command that changes a VM will be issued. "
                "Scaling decisions are logged as they would be taken."
            )

        # Set by SIGTERM/SIGINT so the loop can stop between VMs instead of
        # being killed wherever it happens to be - which, with no handler at
        # all, meant every `systemctl stop` was an abrupt kill.
        self._shutdown = threading.Event()
        self._install_signal_handlers()

        # Initialize billing tracker if enabled
        self.billing_enabled = self.config.get('billing', {}).get('enabled', False)
        self.billing_tracker = None
        if self.billing_enabled:
            try:
                self.billing_tracker = BillingTracker(self.config, self.logger)
                self.logger.info("Billing tracking enabled")
            except BillingDataError as e:
                # Refusing to start would stop the fleet scaling because of a
                # billing file. Refusing to *write* preserves the history and
                # keeps the primary job running.
                self.logger.critical(
                    f"Billing is enabled but its history could not be read: {e}. "
                    "Continuing without billing so scaling is not interrupted. "
                    "The unreadable file has been preserved and nothing will "
                    "overwrite it."
                )
                self.metrics.set('vm_autoscale_billing_degraded', 1)

    def _install_signal_handlers(self) -> None:
        """Stop cleanly on SIGTERM and SIGINT.

        `run()` only ever caught KeyboardInterrupt, which is SIGINT. systemctl
        stop sends SIGTERM, whose default disposition terminates the process
        immediately - including mid-write of the billing state file.
        """
        def handle(signum, _frame):
            name = signal.Signals(signum).name
            self.logger.info(f"Received {name}; finishing the current VM and stopping.")
            self._shutdown.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, handle)
            except ValueError:
                # Not the main thread (tests, embedding). Nothing to install.
                self.logger.debug(f"Could not install a handler for {sig!r}.")

    @staticmethod
    def _load_config(config_path: str) -> dict[str, Any]:
        """Load and validate configuration file."""
        if not Path(config_path).exists():
            raise FileNotFoundError(f"Configuration file not found at {config_path}")

        with open(config_path) as config_file:
            config = yaml.safe_load(config_file)

        # Full validation: types, ranges, unknown keys, referential integrity.
        # The previous check only asserted that four top-level keys existed,
        # which is why a documented key could be written, never read, and never
        # complained about.
        warnings = validate_config(config)
        config['_validation_warnings'] = warnings
        return config

    def _report_config_warnings(self) -> None:
        """Log what validation flagged as suspicious but not fatal."""
        for warning in self.config.pop('_validation_warnings', []):
            self.logger.warning(f"Configuration: {warning}")

    def _setup_logging(self, logging_config_path: str | None) -> logging.Logger:
        """Setup logging configuration."""
        if logging_config_path and Path(logging_config_path).exists():
            with open(logging_config_path) as logging_file:
                logging_config = json.load(logging_file)
                logging.config.dictConfig(logging_config)
        else:
            logging.basicConfig(
                level=self.config.get('logging', {}).get('level', 'INFO'),
                format="%(asctime)s [%(levelname)s] %(message)s",
                handlers=[
                    logging.FileHandler(self.config.get('logging', {}).get('log_file', '/var/log/vm_autoscale.log')),
                    logging.StreamHandler()
                ]
            )
        return logging.getLogger("vm_autoscale")

    def process_vm(self, host: dict[str, Any], vm: dict[str, Any]) -> None:
        """Evaluate and, if warranted, resize one VM.

        Kept to the shape of the decision - connect, gate, read, act - with the
        detail of each step in its own method. This was one 95-line block
        interleaving SSH lifecycle, two gates, metrics emission, billing state,
        threshold resolution and two near-identical scaling branches.
        """
        ssh_client = None
        try:
            ssh_client = self._connect(host)
            vm_manager = self._get_vm_manager(ssh_client, vm)
            # Fresh connection, fresh cycle: nothing carried over from last time.
            vm_manager.invalidate_cache()

            if not self._guest_is_running(vm, vm_manager):
                return
            if not self._host_has_headroom(host, ssh_client):
                return

            cpu_usage, ram_usage = self._read_usage(vm, vm_manager)
            if cpu_usage is None and ram_usage is None:
                self.logger.warning(
                    f"VM {vm['vm_id']}: no usage metrics available this cycle. "
                    "Skipping scaling rather than treating the VM as idle."
                )
                return

            self._apply_scaling(vm, vm_manager, 'cpu', cpu_usage)
            self._apply_scaling(vm, vm_manager, 'ram', ram_usage)

        except Exception as e:
            self.metrics.inc('vm_autoscale_vm_errors_total', {'vm_id': str(vm['vm_id'])})
            self.logger.error(f"Error processing VM {vm['vm_id']} on host {host['name']}: {e}")
            self.notification_manager.send_notification(
                f"Error processing VM {vm['vm_id']} on host {host['name']}: {e}",
                priority=9
            )
        finally:
            if ssh_client:
                ssh_client.close()

    def _connect(self, host: dict[str, Any]) -> SSHClient:
        """Open an SSH connection to one node."""
        ssh_client = SSHClient(
            host=host['host'],
            port=host.get('ssh_port', 22),
            user=host['ssh_user'],
            password=host.get('ssh_password'),
            key_path=host.get('ssh_key'),
            host_key_policy=self.config.get('ssh_host_key_policy', 'accept-new'),
            known_hosts=self.config.get('ssh_known_hosts', DEFAULT_KNOWN_HOSTS),
        )
        ssh_client.connect()
        return ssh_client

    def _guest_is_running(self, vm: dict[str, Any],
                          vm_manager: VMResourceManager) -> bool:
        """Gate 1, and the point at which billing learns about a transition."""
        running = vm_manager.is_vm_running()
        self.metrics.set('vm_autoscale_vm_running', 1 if running else 0,
                         {'vm_id': str(vm['vm_id'])})
        self._record_vm_state(vm['vm_id'], running)
        if not running:
            self.logger.info(f"VM {vm['vm_id']} is not running. Skipping scaling.")
        return running

    def _host_has_headroom(self, host: dict[str, Any], ssh_client: SSHClient) -> bool:
        """Gate 2: never push a node that is already at its ceiling."""
        host_checker = HostResourceChecker(ssh_client)
        within_limits = host_checker.check_host_resources(
            self.config['host_limits']['max_host_cpu_percent'],
            self.config['host_limits']['max_host_ram_percent'])
        self._record_host_metrics(host['name'], host_checker)

        if not within_limits:
            self.metrics.inc('vm_autoscale_host_gate_blocked_total',
                             {'host': host['name']})
            self.logger.warning(
                f"Host {host['name']} resources maxed out. Skipping scaling.")
        return within_limits

    def _read_usage(self, vm: dict[str, Any], vm_manager: VMResourceManager):
        """Current CPU and RAM usage. Either may be None, which is not zero."""
        cpu_usage, ram_usage = vm_manager.get_resource_usage()
        self.logger.info(
            f"VM {vm['vm_id']} current usage - "
            f"CPU: {self._format_usage(cpu_usage)}, "
            f"RAM: {self._format_usage(ram_usage)}"
        )
        self._record_usage_metrics(vm['vm_id'], cpu_usage, ram_usage)
        return cpu_usage, ram_usage

    def _apply_scaling(self, vm: dict[str, Any], vm_manager: VMResourceManager,
                       resource: str, usage: float | None) -> None:
        """Evaluate one resource. CPU and RAM differ only in their names here.

        A failure on one resource is logged and counted but does not stop the
        other from being evaluated.
        """
        if not vm.get(f'{resource}_scaling', False):
            return

        if usage is None:
            self.logger.warning(
                f"VM {vm['vm_id']}: {resource.upper()} usage unavailable; "
                f"skipping {resource.upper()} scaling."
            )
            return

        handler = (self._handle_cpu_scaling if resource == 'cpu'
                   else self._handle_ram_scaling)
        try:
            handler(vm_manager, vm['vm_id'], usage, self._thresholds_for(vm, resource))
            self.logger.debug(
                f"{resource.upper()} scaling completed for VM {vm['vm_id']}")
        except Exception as e:
            self.metrics.inc('vm_autoscale_scaling_failures_total',
                             {'vm_id': str(vm['vm_id']), 'resource': resource})
            self.logger.error(
                f"{resource.upper()} scaling failed for VM {vm['vm_id']}: {e!s}")

    def _start_metrics_server(self) -> MetricsServer | None:
        """Start the Prometheus endpoint when it is enabled in the config.

        Off by default, and bound to localhost when on: this process holds root
        credentials, and the series it exposes name your nodes and VMIDs with
        no authentication in front of them.
        """
        cfg = self.config.get('metrics') or {}
        if not cfg.get('enabled', False):
            return None

        server = MetricsServer(
            self.metrics, self.logger,
            bind=cfg.get('bind', '127.0.0.1'),
            port=int(cfg.get('port', 9808)),
            path=cfg.get('path', '/metrics'),
        )
        return server if server.start() else None

    def _get_vm_manager(self, ssh_client: SSHClient,
                        vm: Any) -> VMResourceManager:
        """Return the VMResourceManager for a VM, creating it on first use.

        Accepts either the VM's config entry or a bare id. A fresh SSH
        connection is opened every cycle, so the cached manager is rebound to
        the current client. Keeping the manager itself alive is what makes
        `scale_cooldown` meaningful across cycles.
        """
        vm_config = vm if isinstance(vm, dict) else {}
        vm_id = vm['vm_id'] if isinstance(vm, dict) else vm

        key = str(vm_id)
        manager = self._vm_managers.get(key)
        if manager is None:
            manager = VMResourceManager(ssh_client, vm_id, self.config, vm_config)
            self._vm_managers[key] = manager
        else:
            manager.ssh_client = ssh_client
            # Retry hotplug auto-configuration if the first attempt failed;
            # this is a no-op once it has succeeded.
            manager.ensure_hotplug_configured()
        return manager

    def _confirm_scale_down(self, vm_id: Any, resource: str, below: bool) -> bool:
        """Whether a shrink has been observed often enough to act on.

        Scale-up and scale-down are not symmetric operations. Adding capacity
        fails safe; reclaiming memory from a guest that is using it drives it
        into swap or to the OOM killer, and a vCPU unplug may simply be
        refused. A single sample is not evidence enough for the second kind.
        """
        key = (str(vm_id), resource)

        if not below:
            self._low_streaks.pop(key, None)
            return False

        streak = self._low_streaks.get(key, 0) + 1
        self._low_streaks[key] = streak

        if streak < self.scale_down_after:
            self.logger.info(
                f"VM {vm_id}: {resource} below its low threshold "
                f"({streak}/{self.scale_down_after} consecutive readings). "
                "Holding until it is sustained."
            )
            return False

        self._low_streaks.pop(key, None)
        return True

    def _thresholds_for(self, vm: dict[str, Any], resource: str) -> dict[str, float]:
        """Resolve the high/low thresholds for one VM and one resource.

        Falls back to the global `scaling_thresholds` section. A VM may override
        either or both bounds, in the flat shape shown in `config.yaml`:

            thresholds:
              cpu_high: 90
              cpu_low: 30

        or in the same nested shape as the global section:

            thresholds:
              cpu: { high: 90, low: 30 }

        Both were previously ignored entirely - the block existed in the example
        config but nothing read it.
        """
        thresholds = dict(self.config['scaling_thresholds'][resource])

        overrides = vm.get('thresholds') or {}
        if not isinstance(overrides, dict):
            self.logger.warning(
                f"VM {vm.get('vm_id')}: 'thresholds' is not a mapping; ignoring it."
            )
            return thresholds

        nested = overrides.get(resource)
        if isinstance(nested, dict):
            for bound in ('high', 'low'):
                if nested.get(bound) is not None:
                    thresholds[bound] = nested[bound]

        for bound in ('high', 'low'):
            value = overrides.get(f"{resource}_{bound}")
            if value is not None:
                thresholds[bound] = value

        if thresholds['low'] > thresholds['high']:
            self.logger.warning(
                f"VM {vm.get('vm_id')}: {resource} low threshold "
                f"({thresholds['low']}) is above high ({thresholds['high']}); "
                "using the global values instead."
            )
            return dict(self.config['scaling_thresholds'][resource])

        return thresholds

    def _record_host_metrics(self, host_name: str, checker: HostResourceChecker) -> None:
        """Publish the node readings the gate just used."""
        if checker.last_cpu_percent is not None:
            self.metrics.set('vm_autoscale_host_cpu_percent',
                             checker.last_cpu_percent, {'host': host_name})
        if checker.last_ram_percent is not None:
            self.metrics.set('vm_autoscale_host_ram_percent',
                             checker.last_ram_percent, {'host': host_name})

    def _record_usage_metrics(self, vm_id: Any, cpu: float | None,
                              ram: float | None) -> None:
        """Publish guest usage, dropping the series when it is unreadable.

        An absent series is not the same as a zero one. Emitting 0 for a metric
        that could not be read would put the same lie into your dashboards that
        it used to put into the scaling decision.
        """
        labels = {'vm_id': str(vm_id)}
        for name, value, resource in (
            ('vm_autoscale_vm_cpu_percent', cpu, 'cpu'),
            ('vm_autoscale_vm_ram_percent', ram, 'ram'),
        ):
            if value is None:
                self.metrics.unset(name, labels)
                self.metrics.inc('vm_autoscale_metric_unavailable_total',
                                 {'vm_id': str(vm_id), 'resource': resource})
            else:
                self.metrics.set(name, value, labels)

    @staticmethod
    def _format_usage(value: float | None) -> str:
        """Render a usage figure for the log, distinguishing unknown from zero."""
        return "unavailable" if value is None else f"{value:.2f}%"

    def _handle_cpu_scaling(self, vm_manager: VMResourceManager, vm_id: int,
                            cpu_usage: float | None,
                            thresholds: dict[str, float] | None = None) -> None:
        """Handle CPU scaling decisions. A None reading is never acted on."""
        if cpu_usage is None:
            return
        thresholds = thresholds or self.config['scaling_thresholds']['cpu']
        below = cpu_usage < thresholds['low']
        sustained = self._confirm_scale_down(vm_id, 'cpu', below)
        if cpu_usage > thresholds['high']:
            self._low_streaks.pop((str(vm_id), 'cpu'), None)
            if vm_manager.scale_cpu('up'):
                self.metrics.inc('vm_autoscale_scaling_actions_total',
                                 {'vm_id': str(vm_id), 'resource': 'cpu',
                                  'direction': 'up'})
                self.notification_manager.send_notification(
                    f"Scaled up CPU for VM {vm_id} due to high usage ({cpu_usage}%).",
                    priority=7
                )
                # Record for billing
                if self.billing_tracker:
                    self._record_billing_spec(vm_manager, vm_id)
        elif sustained:
            if vm_manager.scale_cpu('down'):
                self.metrics.inc('vm_autoscale_scaling_actions_total',
                                 {'vm_id': str(vm_id), 'resource': 'cpu',
                                  'direction': 'down'})
                self.notification_manager.send_notification(
                    f"Scaled down CPU for VM {vm_id} due to low usage ({cpu_usage}%).",
                    priority=5
                )
                # Record for billing
                if self.billing_tracker:
                    self._record_billing_spec(vm_manager, vm_id)

    def _handle_ram_scaling(self, vm_manager: VMResourceManager, vm_id: int,
                            ram_usage: float | None,
                            thresholds: dict[str, float] | None = None) -> None:
        """Handle RAM scaling decisions. A None reading is never acted on."""
        if ram_usage is None:
            return
        thresholds = thresholds or self.config['scaling_thresholds']['ram']
        below = ram_usage < thresholds['low']
        sustained = self._confirm_scale_down(vm_id, 'ram', below)
        if ram_usage > thresholds['high']:
            self._low_streaks.pop((str(vm_id), 'ram'), None)
            if vm_manager.scale_ram('up'):
                self.metrics.inc('vm_autoscale_scaling_actions_total',
                                 {'vm_id': str(vm_id), 'resource': 'ram',
                                  'direction': 'up'})
                self.notification_manager.send_notification(
                    f"Scaled up RAM for VM {vm_id} due to high usage ({ram_usage}%).",
                    priority=7
                )
                # Record for billing
                if self.billing_tracker:
                    self._record_billing_spec(vm_manager, vm_id)
        elif sustained:
            if vm_manager.scale_ram('down'):
                self.metrics.inc('vm_autoscale_scaling_actions_total',
                                 {'vm_id': str(vm_id), 'resource': 'ram',
                                  'direction': 'down'})
                self.notification_manager.send_notification(
                    f"Scaled down RAM for VM {vm_id} due to low usage ({ram_usage}%).",
                    priority=5
                )
                # Record for billing
                if self.billing_tracker:
                    self._record_billing_spec(vm_manager, vm_id)

    def _record_vm_state(self, vm_id: Any, running: bool) -> None:
        """Record a start/stop transition for billing.

        Only transitions are written, so the state history stays proportional
        to how often VMs actually change state rather than to the poll rate.
        Nothing called this before, which is why every billing report showed
        100% uptime.
        """
        if not self.billing_tracker or self.dry_run:
            return

        key = str(vm_id)
        if self._vm_states.get(key) == running:
            return
        self._vm_states[key] = running

        try:
            self.billing_tracker.record_vm_state_change(
                key, 'started' if running else 'stopped'
            )
        except Exception as e:
            self.logger.warning(f"Failed to record billing state for VM {vm_id}: {e}")

    def _maybe_generate_billing_reports(self) -> None:
        """Emit period reports once a full billing period has elapsed.

        `generate_period_report` existed but nothing called it, so enabling
        billing produced a growing state file and no CSV, no webhook and no
        report of any kind.
        """
        if not self.billing_tracker or self.dry_run:
            return

        try:
            if not self.billing_tracker.is_period_due():
                return
        except Exception as e:
            self.logger.error(f"Failed to check the billing period: {e}")
            return

        self.logger.info("Billing period elapsed; generating reports.")
        generated = 0
        for vm in self.config.get('virtual_machines', []):
            try:
                report = self.billing_tracker.generate_period_report(str(vm['vm_id']))
                if report:
                    generated += 1
                    self.logger.info(
                        f"Billing: VM {report.vm_id} cost {report.total_cost:.4f} "
                        f"over {report.total_uptime_hours:.2f} uptime hours"
                    )
            except Exception as e:
                self.logger.error(
                    f"Failed to generate a billing report for VM {vm.get('vm_id')}: {e}"
                )

        self.billing_tracker.set_last_report_time()
        self.logger.info(f"Billing: generated {generated} report(s).")

    def _record_billing_spec(self, vm_manager: VMResourceManager, vm_id: int) -> None:
        """Record current VM spec for billing after a scaling operation."""
        if self.dry_run:
            # Nothing was changed, so there is no new spec to bill for.
            return
        try:
            current_cores = vm_manager._get_current_cores()
            current_ram = vm_manager._get_current_ram()
            self.billing_tracker.record_spec_change(
                vm_id=str(vm_id),
                cpu_cores=current_cores,
                ram_mb=current_ram
            )
        except Exception as e:
            self.logger.warning(f"Failed to record billing spec for VM {vm_id}: {e}")

    def run(self) -> None:
        """Main execution loop. Returns when a shutdown signal is received."""
        self.logger.info(f"Starting VM Autoscaler {__version__}")
        while not self._shutdown.is_set():
            try:
                cycle_started = time.monotonic()
                self._run_cycle()

                self._maybe_generate_billing_reports()

                self.metrics.inc('vm_autoscale_cycles_total')
                self.metrics.set('vm_autoscale_cycle_duration_seconds',
                                 time.monotonic() - cycle_started)
                self.metrics.set('vm_autoscale_last_cycle_timestamp_seconds', time.time())

                check_interval = self.config.get('check_interval', 300)  # Default to 5 minutes
                # Waiting on the event rather than sleeping means a stop signal
                # is acted on immediately instead of up to check_interval later.
                self._shutdown.wait(check_interval)

            except KeyboardInterrupt:
                self._shutdown.set()
            except Exception as e:
                self.metrics.inc('vm_autoscale_cycle_errors_total')
                self.logger.error(f"Unexpected error in main loop: {e}")
                self.notification_manager.send_notification(
                    f"Unexpected error in VM Autoscaler: {e}",
                    priority=10
                )
                self._shutdown.wait(60)  # Wait before retrying

        self.shutdown()

    def _run_cycle(self) -> None:
        """One pass over every enabled VM, abandoned early on a stop signal."""
        for host in self.config['proxmox_hosts']:
            for vm in self.config['virtual_machines']:
                if self._shutdown.is_set():
                    return
                if vm['proxmox_host'] == host['name'] and vm.get('scaling_enabled', False):
                    self.process_vm(host, vm)

    def shutdown(self) -> None:
        """Release what the process owns before exiting."""
        self.logger.info("Shutting down VM Autoscaler")
        self.metrics.set('vm_autoscale_up', 0)
        if self._metrics_server is not None:
            self._metrics_server.stop()
            self._metrics_server = None

def main():
    """Entry point of the application."""
    try:
        autoscaler = VMAutoscaler(
            config_path="/usr/local/bin/vm_autoscale/config.yaml",
            logging_config_path="/usr/local/bin/vm_autoscale/logging_config.json"
        )
        autoscaler.run()
    except ConfigurationInvalid as e:
        # Every problem at once, one per line: one restart is enough to see
        # all of them rather than peeling them off one at a time.
        logging.critical("Refusing to start. %s", e)
        sys.exit(2)
    except Exception as e:
        logging.critical(f"Failed to start VM Autoscaler: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
