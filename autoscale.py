import yaml
import json
import requests
import smtplib
import logging
import logging.config
import time
from ssh_utils import SSHClient
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from vm_manager import VMResourceManager
from host_resource_checker import HostResourceChecker
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

            msg = MIMEMultipart()
            msg['From'] = smtp_config['user']
            msg['To'] = ", ".join(to_emails)
            msg['Subject'] = "VM Autoscale Alert"
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
                user=host['ssh_user'],
                password=host.get('ssh_password'),
                key_path=host.get('ssh_key')
            )
            ssh_client.connect()

            vm_manager = VMResourceManager(ssh_client, vm['vm_id'], self.config)
            if not vm_manager.is_vm_running():
                self.logger.info(f"VM {vm['vm_id']} is not running. Skipping scaling.")
                return

            host_checker = HostResourceChecker(ssh_client)
            if not host_checker.check_host_resources(
                    self.config['host_limits']['max_host_cpu_percent'],
                    self.config['host_limits']['max_host_ram_percent']):
                self.logger.warning(f"Host {host['name']} resources maxed out. Scaling restricted.")
                self.notification_manager.send_notification(
                    f"Host {host['name']} resources maxed out. Scaling restricted.",
                    priority=8
                )
                return

            # Scaling logic
            vm_cpu_usage, vm_ram_usage = vm_manager.get_resource_usage()
            self.logger.info(f"VM {vm['vm_id']} - CPU: {vm_cpu_usage}%, RAM: {vm_ram_usage}%")

            if vm['cpu_scaling']:
                self._handle_cpu_scaling(vm_manager, vm['vm_id'], vm_cpu_usage)

            if vm['ram_scaling']:
                self._handle_ram_scaling(vm_manager, vm['vm_id'], vm_ram_usage)

        except Exception as e:
            self.logger.error(f"Error processing VM {vm['vm_id']} on host {host['name']}: {e}")
            self.notification_manager.send_notification(
                f"Error processing VM {vm['vm_id']} on host {host['name']}: {e}",
                priority=9
            )
        finally:
            if ssh_client:
                ssh_client.close()

    def _handle_cpu_scaling(self, vm_manager: VMResourceManager, vm_id: int, cpu_usage: float) -> None:
        """Handle CPU scaling decisions."""
        thresholds = self.config['scaling_thresholds']['cpu']
        if cpu_usage > thresholds['high']:
            if vm_manager.scale_cpu('up'):
                self.notification_manager.send_notification(
                    f"Scaled up CPU for VM {vm_id} due to high usage ({cpu_usage}%).",
                    priority=7
                )
        elif cpu_usage < thresholds['low']:
            if vm_manager.scale_cpu('down'):
                self.notification_manager.send_notification(
                    f"Scaled down CPU for VM {vm_id} due to low usage ({cpu_usage}%).",
                    priority=5
                )

    def _handle_ram_scaling(self, vm_manager: VMResourceManager, vm_id: int, ram_usage: float) -> None:
        """Handle RAM scaling decisions."""
        thresholds = self.config['scaling_thresholds']['ram']
        if ram_usage > thresholds['high']:
            if vm_manager.scale_ram('up'):
                self.notification_manager.send_notification(
                    f"Scaled up RAM for VM {vm_id} due to high usage ({ram_usage}%).",
                    priority=7
                )
        elif ram_usage < thresholds['low']:
            if vm_manager.scale_ram('down'):
                self.notification_manager.send_notification(
                    f"Scaled down RAM for VM {vm_id} due to low usage ({ram_usage}%).",
                    priority=5
                )

    def run(self) -> None:
        """Main execution loop."""
        self.logger.info("Starting VM Autoscaler")
        while True:
            try:
                for host in self.config['proxmox_hosts']:
                    for vm in self.config['virtual_machines']:
                        if vm['proxmox_host'] == host['name'] and vm.get('scaling_enabled', False):
                            self.process_vm(host, vm)
                
                check_interval = self.config.get('check_interval', 300)  # Default to 5 minutes
                time.sleep(check_interval)
            
            except KeyboardInterrupt:
                self.logger.info("Shutting down VM Autoscaler")
                break
            except Exception as e:
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