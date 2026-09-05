import yaml
import json
import requests
import smtplib
import logging
import logging.config
import time
import re
import sys
from ssh_utils import DEFAULT_KNOWN_HOSTS, SSHClient
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from vm_manager import VMResourceManager
from host_resource_checker import HostResourceChecker
from billing_tracker import BillingTracker
from metrics import MetricsServer, build_registry
from functools import wraps
from typing import Union, List, Optional, Dict, Any

class ConfigurationError(Exception):
    """Custom exception for configuration-related errors."""
    pass

class NotificationManager:
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.validate_notification_config()

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

    def _format_message(self, message: Union[str, tuple, Any]) -> str:
        """Format message to ensure it's a string."""
        if isinstance(message, tuple):
            # If it's a tuple, join non-empty parts
            return ' '.join(str(part) for part in message if part)
        elif isinstance(message, str):
            return message
        else:
            return str(message)

    def send_gotify_notification(self, message: str, priority: Optional[int] = None) -> None:
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
            self.logger.error(f"Failed to send Gotify notification: {str(e)}")
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
            if result:
                vm_id = result.group(1)
            else:
                vm_id = ""
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
            self.logger.error(f"Failed to send email notification: {str(e)}")
            raise

    def send_notification(self, message: Union[str, tuple, Any], priority: Optional[int] = None) -> None:
        """Send notification through configured channels."""
        sent = False
        errors = []
        formatted_message = self._format_message(message)
        if self.config.get('dry_run', False):
            formatted_message = f"[DRY RUN] {formatted_message}"

        if self.config.get('gotify', {}).get('enabled', False):
            try:
                self.send_gotify_notification(formatted_message, priority)
                sent = True
            except Exception as e:
                error_msg = f"Failed to send Gotify notification: {str(e)}"
                errors.append(error_msg)
                self.logger.error(error_msg)

        if self.config.get('alerts', {}).get('email_enabled', False):
            try:
                self.send_smtp_notification(formatted_message)
                sent = True
            except Exception as e:
                error_msg = f"Failed to send email notification: {str(e)}"
                errors.append(error_msg)
                self.logger.error(error_msg)

        if not sent:
            error_summary = f" Errors: {'; '.join(errors)}" if errors else ""
            self.logger.warning(
                f"Failed to send notification through any channel. Message: {formatted_message}.{error_summary}"
            )

class VMAutoscaler:
    def __init__(self, config_path: str, logging_config_path: Optional[str] = None):
        self.config = self._load_config(config_path)
        self.logger = self._setup_logging(logging_config_path)
        self.notification_manager = NotificationManager(self.config, self.logger)
        # VMResourceManager instances are reused across polling cycles so the
        # scaling cooldown survives between iterations of the main loop, and so
        # hotplug auto-configuration runs once per VM instead of every cycle.
        self._vm_managers: Dict[str, VMResourceManager] = {}
        # Last observed running state per VM, so billing records transitions
        # rather than one entry per poll.
        self._vm_states: Dict[str, bool] = {}
        self.dry_run = bool(self.config.get('dry_run', False))
        self.metrics = build_registry()
        self.metrics.set('vm_autoscale_build_info', 1,
                         {'dry_run': str(self.dry_run).lower()})
        self._metrics_server = self._start_metrics_server()
        if self.dry_run:
            self.logger.warning(
                "DRY RUN: no command that changes a VM will be issued. "
                "Scaling decisions are logged as they would be taken."
            )
        
        # Initialize billing tracker if enabled
        self.billing_enabled = self.config.get('billing', {}).get('enabled', False)
        if self.billing_enabled:
            self.billing_tracker = BillingTracker(self.config, self.logger)
            self.logger.info("Billing tracking enabled")
        else:
            self.billing_tracker = None

    @staticmethod
    def _load_config(config_path: str) -> Dict[str, Any]:
        """Load and validate configuration file."""
        if not Path(config_path).exists():
            raise FileNotFoundError(f"Configuration file not found at {config_path}")
        
        with open(config_path, 'r') as config_file:
            config = yaml.safe_load(config_file)
        
        # Validate essential configuration
        required_sections = ['scaling_thresholds', 'scaling_limits', 'proxmox_hosts', 'virtual_machines']
        missing_sections = [section for section in required_sections if section not in config]
        if missing_sections:
            raise ConfigurationError(f"Missing required configuration sections: {', '.join(missing_sections)}")
            
        return config

    def _setup_logging(self, logging_config_path: Optional[str]) -> logging.Logger:
        """Setup logging configuration."""
        if logging_config_path and Path(logging_config_path).exists():
            with open(logging_config_path, 'r') as logging_file:
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

    def process_vm(self, host: Dict[str, Any], vm: Dict[str, Any]) -> None:
        """Process a single VM for autoscaling."""
        ssh_client = None
        try:
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

            vm_manager = self._get_vm_manager(ssh_client, vm)

            # First check if VM is running
            running = vm_manager.is_vm_running()
            self.metrics.set('vm_autoscale_vm_running', 1 if running else 0,
                             {'vm_id': str(vm['vm_id'])})
            self._record_vm_state(vm['vm_id'], running)
            if not running:
                self.logger.info(f"VM {vm['vm_id']} is not running. Skipping scaling.")
                return

            # Check host resources first
            host_checker = HostResourceChecker(ssh_client)
            within_limits = host_checker.check_host_resources(
                self.config['host_limits']['max_host_cpu_percent'],
                self.config['host_limits']['max_host_ram_percent'])
            self._record_host_metrics(host['name'], host_checker)
            if not within_limits:
                self.metrics.inc('vm_autoscale_host_gate_blocked_total',
                                 {'host': host['name']})
                self.logger.warning(f"Host {host['name']} resources maxed out. Skipping scaling.")
                return

            # Get current resource usage once to avoid multiple calls.
            # Either figure is None when it could not be read; that is not the
            # same as zero, and must never be fed to a scaling decision.
            current_cpu_usage, current_ram_usage = vm_manager.get_resource_usage()
            self.logger.info(
                f"VM {vm['vm_id']} current usage - "
                f"CPU: {self._format_usage(current_cpu_usage)}, "
                f"RAM: {self._format_usage(current_ram_usage)}"
            )

            self._record_usage_metrics(vm['vm_id'], current_cpu_usage, current_ram_usage)

            if current_cpu_usage is None and current_ram_usage is None:
                self.logger.warning(
                    f"VM {vm['vm_id']}: no usage metrics available this cycle. "
                    "Skipping scaling rather than treating the VM as idle."
                )
                return

            # Handle CPU scaling if enabled
            if vm.get('cpu_scaling', False):
                if current_cpu_usage is None:
                    self.logger.warning(
                        f"VM {vm['vm_id']}: CPU usage unavailable; skipping CPU scaling."
                    )
                else:
                    try:
                        self._handle_cpu_scaling(
                            vm_manager, vm['vm_id'], current_cpu_usage,
                            self._thresholds_for(vm, 'cpu'),
                        )
                        self.logger.debug(f"CPU scaling completed for VM {vm['vm_id']}")
                    except Exception as e:
                        self.metrics.inc('vm_autoscale_scaling_failures_total',
                                         {'vm_id': str(vm['vm_id']), 'resource': 'cpu'})
                        self.logger.error(f"CPU scaling failed for VM {vm['vm_id']}: {str(e)}")
                        # Continue to RAM scaling even if CPU scaling fails

            # Handle RAM scaling if enabled
            if vm.get('ram_scaling', False):
                if current_ram_usage is None:
                    self.logger.warning(
                        f"VM {vm['vm_id']}: RAM usage unavailable; skipping RAM scaling."
                    )
                else:
                    try:
                        self._handle_ram_scaling(
                            vm_manager, vm['vm_id'], current_ram_usage,
                            self._thresholds_for(vm, 'ram'),
                        )
                        self.logger.debug(f"RAM scaling completed for VM {vm['vm_id']}")
                    except Exception as e:
                        self.metrics.inc('vm_autoscale_scaling_failures_total',
                                         {'vm_id': str(vm['vm_id']), 'resource': 'ram'})
                        self.logger.error(f"RAM scaling failed for VM {vm['vm_id']}: {str(e)}")

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

    def _start_metrics_server(self) -> Optional[MetricsServer]:
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

    def _thresholds_for(self, vm: Dict[str, Any], resource: str) -> Dict[str, float]:
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

    def _record_usage_metrics(self, vm_id: Any, cpu: Optional[float],
                              ram: Optional[float]) -> None:
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
    def _format_usage(value: Optional[float]) -> str:
        """Render a usage figure for the log, distinguishing unknown from zero."""
        return "unavailable" if value is None else f"{value:.2f}%"

    def _handle_cpu_scaling(self, vm_manager: VMResourceManager, vm_id: int,
                            cpu_usage: Optional[float],
                            thresholds: Optional[Dict[str, float]] = None) -> None:
        """Handle CPU scaling decisions. A None reading is never acted on."""
        if cpu_usage is None:
            return
        thresholds = thresholds or self.config['scaling_thresholds']['cpu']
        if cpu_usage > thresholds['high']:
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
        elif cpu_usage < thresholds['low']:
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
                            ram_usage: Optional[float],
                            thresholds: Optional[Dict[str, float]] = None) -> None:
        """Handle RAM scaling decisions. A None reading is never acted on."""
        if ram_usage is None:
            return
        thresholds = thresholds or self.config['scaling_thresholds']['ram']
        if ram_usage > thresholds['high']:
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
        elif ram_usage < thresholds['low']:
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
        """Main execution loop."""
        self.logger.info("Starting VM Autoscaler")
        while True:
            try:
                cycle_started = time.monotonic()
                for host in self.config['proxmox_hosts']:
                    for vm in self.config['virtual_machines']:
                        if vm['proxmox_host'] == host['name'] and vm.get('scaling_enabled', False):
                            self.process_vm(host, vm)

                self._maybe_generate_billing_reports()

                self.metrics.inc('vm_autoscale_cycles_total')
                self.metrics.set('vm_autoscale_cycle_duration_seconds',
                                 time.monotonic() - cycle_started)
                self.metrics.set('vm_autoscale_last_cycle_timestamp_seconds', time.time())

                check_interval = self.config.get('check_interval', 300)  # Default to 5 minutes
                time.sleep(check_interval)
            
            except KeyboardInterrupt:
                self.logger.info("Shutting down VM Autoscaler")
                break
            except Exception as e:
                self.metrics.inc('vm_autoscale_cycle_errors_total')
                self.logger.error(f"Unexpected error in main loop: {e}")
                self.notification_manager.send_notification(
                    f"Unexpected error in VM Autoscaler: {e}",
                    priority=10
                )
                time.sleep(60)  # Wait before retrying

def main():
    """Entry point of the application."""
    try:
        autoscaler = VMAutoscaler(
            config_path="/usr/local/bin/vm_autoscale/config.yaml",
            logging_config_path="/usr/local/bin/vm_autoscale/logging_config.json"
        )
        autoscaler.run()
    except Exception as e:
        logging.critical(f"Failed to start VM Autoscaler: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
