"""
Billing Tracker Module for VM Autoscale

Tracks resource changes and calculates costs for billing web hosters.
Records CPU/RAM spec changes and VM state transitions, then generates
billing reports per period.
"""

import csv
import json
import logging
import os
import subprocess
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from pathlib import Path


@dataclass
class SpecChangeRecord:
    """Record of a VM spec change."""
    timestamp: datetime
    cpu_cores: int
    ram_mb: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "cpu_cores": self.cpu_cores,
            "ram_mb": self.ram_mb
        }


@dataclass
class StateChangeRecord:
    """Record of a VM state change (started/stopped)."""
    timestamp: datetime
    state: str  # 'started' or 'stopped'
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "state": self.state
        }


@dataclass
class BillingReport:
    """Billing report for a VM over a billing period."""
    vm_id: str
    vm_name: str
    period_start: datetime
    period_end: datetime
    min_cpu_cores: int
    max_cpu_cores: int
    avg_cpu_cores: float
    min_ram_mb: int
    max_ram_mb: int
    avg_ram_mb: float
    total_uptime_hours: float
    total_downtime_hours: float
    uptime_percentage: float
    spec_changes: List[Dict[str, Any]]
    total_cost: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "vm_id": self.vm_id,
            "vm_name": self.vm_name,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "min_cpu_cores": self.min_cpu_cores,
            "max_cpu_cores": self.max_cpu_cores,
            "avg_cpu_cores": round(self.avg_cpu_cores, 2),
            "min_ram_mb": self.min_ram_mb,
            "max_ram_mb": self.max_ram_mb,
            "avg_ram_mb": round(self.avg_ram_mb, 2),
            "total_uptime_hours": round(self.total_uptime_hours, 2),
            "total_downtime_hours": round(self.total_downtime_hours, 2),
            "uptime_percentage": round(self.uptime_percentage, 2),
            "spec_changes": self.spec_changes,
            "total_cost": round(self.total_cost, 4)
        }


class BillingTracker:
    """
    Tracks VM resource usage and calculates billing for autoscaled resources.
    
    Usage:
        tracker = BillingTracker(config, logger)
        tracker.record_spec_change(vm_id, cpu_cores, ram_mb)
        tracker.record_vm_state_change(vm_id, 'started')
        report = tracker.calculate_billing_period(vm_id, start_date, end_date)
        tracker.export_csv(report, output_path)
    """
    
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.billing_config = config.get('billing', {})
        
        # Storage for records (in production, this would be persisted to disk/DB)
        self._spec_changes: Dict[str, List[SpecChangeRecord]] = {}
        self._state_changes: Dict[str, List[StateChangeRecord]] = {}
        self._vm_names: Dict[str, str] = {}
        
        # Billing parameters
        self.cost_per_cpu_hour = self.billing_config.get('cost_per_cpu_core_per_hour', 0.01)
        self.cost_per_gb_ram_hour = self.billing_config.get('cost_per_gb_ram_per_hour', 0.005)
        self.billing_period_days = self.billing_config.get('billing_period_days', 30)
        self.csv_output_dir = self.billing_config.get('csv_output_dir', '/var/log/vm_autoscale/billing/')
        self.webhook_script = self.billing_config.get('webhook_script', '')
        self.webhook_url = self.billing_config.get('webhook_url', '')
        
        # Ensure output directory exists
        Path(self.csv_output_dir).mkdir(parents=True, exist_ok=True)
        
        # Load persisted data if exists
        self._load_data()
    
    def _get_data_file_path(self) -> str:
        """Get path to the persisted billing data file."""
        return os.path.join(self.csv_output_dir, 'billing_data.json')
    
    def _load_data(self) -> None:
        """Load persisted billing data from disk."""
        data_file = self._get_data_file_path()
        if os.path.exists(data_file):
            try:
                with open(data_file, 'r') as f:
                    data = json.load(f)
                
                # Restore spec changes
                for vm_id, records in data.get('spec_changes', {}).items():
                    self._spec_changes[vm_id] = [
                        SpecChangeRecord(
                            timestamp=datetime.fromisoformat(r['timestamp']),
                            cpu_cores=r['cpu_cores'],
                            ram_mb=r['ram_mb']
                        ) for r in records
                    ]
                
                # Restore state changes
                for vm_id, records in data.get('state_changes', {}).items():
                    self._state_changes[vm_id] = [
                        StateChangeRecord(
                            timestamp=datetime.fromisoformat(r['timestamp']),
                            state=r['state']
                        ) for r in records
                    ]
                
                self._vm_names = data.get('vm_names', {})
                self.logger.debug(f"Loaded billing data from {data_file}")
            except Exception as e:
                self.logger.warning(f"Failed to load billing data: {e}")
    
    def _save_data(self) -> None:
        """Persist billing data to disk."""
        data_file = self._get_data_file_path()
        try:
            data = {
                'spec_changes': {
                    vm_id: [r.to_dict() for r in records]
                    for vm_id, records in self._spec_changes.items()
                },
                'state_changes': {
                    vm_id: [r.to_dict() for r in records]
                    for vm_id, records in self._state_changes.items()
                },
                'vm_names': self._vm_names
            }
            with open(data_file, 'w') as f:
                json.dump(data, f, indent=2)
            self.logger.debug(f"Saved billing data to {data_file}")
        except Exception as e:
            self.logger.error(f"Failed to save billing data: {e}")
    
    def set_vm_name(self, vm_id: str, vm_name: str) -> None:
        """Set the human-readable name for a VM."""
        self._vm_names[str(vm_id)] = vm_name
        self._save_data()
    
    def record_spec_change(self, vm_id: str, cpu_cores: int, ram_mb: int, 
                           timestamp: Optional[datetime] = None) -> None:
        """
        Record a spec change for a VM.
        
        Args:
            vm_id: The VM identifier
            cpu_cores: Current number of CPU cores
            ram_mb: Current RAM in MB
            timestamp: Optional timestamp (defaults to now)
        """
        vm_id = str(vm_id)
        if vm_id not in self._spec_changes:
            self._spec_changes[vm_id] = []
        
        record = SpecChangeRecord(
            timestamp=timestamp or datetime.now(),
            cpu_cores=cpu_cores,
            ram_mb=ram_mb
        )
        self._spec_changes[vm_id].append(record)
        self._save_data()
        
        self.logger.info(
            f"Billing: Recorded spec change for VM {vm_id}: "
            f"CPU={cpu_cores} cores, RAM={ram_mb} MB"
        )
    
    def record_vm_state_change(self, vm_id: str, state: str,
                                timestamp: Optional[datetime] = None) -> None:
        """
        Record a VM state change (started/stopped).
        
        Args:
            vm_id: The VM identifier
            state: Either 'started' or 'stopped'
            timestamp: Optional timestamp (defaults to now)
        """
        vm_id = str(vm_id)
        if state not in ('started', 'stopped'):
            raise ValueError(f"Invalid state: {state}. Must be 'started' or 'stopped'")
        
        if vm_id not in self._state_changes:
            self._state_changes[vm_id] = []
        
        record = StateChangeRecord(
            timestamp=timestamp or datetime.now(),
            state=state
        )
        self._state_changes[vm_id].append(record)
        self._save_data()
        
        self.logger.info(f"Billing: Recorded VM {vm_id} state change: {state}")
    
    def calculate_billing_period(self, vm_id: str, 
                                  period_start: datetime,
                                  period_end: datetime) -> BillingReport:
        """
        Calculate billing for a VM over a specified period.
        
        Args:
            vm_id: The VM identifier
            period_start: Start of billing period
            period_end: End of billing period
            
        Returns:
            BillingReport with calculated costs and statistics
        """
        vm_id = str(vm_id)
        vm_name = self._vm_names.get(vm_id, f"VM-{vm_id}")
        
        # Get records for this period
        spec_records = [
            r for r in self._spec_changes.get(vm_id, [])
            if period_start <= r.timestamp <= period_end
        ]
        state_records = [
            r for r in self._state_changes.get(vm_id, [])
            if period_start <= r.timestamp <= period_end
        ]
        
        # Calculate CPU/RAM statistics
        if spec_records:
            cpu_values = [r.cpu_cores for r in spec_records]
            ram_values = [r.ram_mb for r in spec_records]
            min_cpu = min(cpu_values)
            max_cpu = max(cpu_values)
            avg_cpu = sum(cpu_values) / len(cpu_values)
            min_ram = min(ram_values)
            max_ram = max(ram_values)
            avg_ram = sum(ram_values) / len(ram_values)
        else:
            min_cpu = max_cpu = avg_cpu = 0
            min_ram = max_ram = avg_ram = 0
        
        # Calculate uptime/downtime
        total_hours = (period_end - period_start).total_seconds() / 3600
        uptime_hours, downtime_hours = self._calculate_uptime(
            state_records, period_start, period_end
        )
        uptime_percentage = (uptime_hours / total_hours * 100) if total_hours > 0 else 0
        
        # Calculate cost (only charge for uptime)
        # Use time-weighted average for resources
        cpu_cost = self._calculate_resource_cost(
            spec_records, 'cpu_cores', self.cost_per_cpu_hour, 
            period_start, period_end, state_records
        )
        ram_cost = self._calculate_resource_cost(
            spec_records, 'ram_mb', self.cost_per_gb_ram_hour / 1024,  # Convert to per-MB
            period_start, period_end, state_records
        )
        total_cost = cpu_cost + ram_cost
        
        return BillingReport(
            vm_id=vm_id,
            vm_name=vm_name,
            period_start=period_start,
            period_end=period_end,
            min_cpu_cores=min_cpu,
            max_cpu_cores=max_cpu,
            avg_cpu_cores=avg_cpu,
            min_ram_mb=min_ram,
            max_ram_mb=max_ram,
            avg_ram_mb=avg_ram,
            total_uptime_hours=uptime_hours,
            total_downtime_hours=downtime_hours,
            uptime_percentage=uptime_percentage,
            spec_changes=[r.to_dict() for r in spec_records],
            total_cost=total_cost
        )
    
    def _calculate_uptime(self, state_records: List[StateChangeRecord],
                          period_start: datetime, 
                          period_end: datetime) -> tuple:
        """Calculate total uptime and downtime hours for a period."""
        if not state_records:
            # Assume always up if no state records
            total_hours = (period_end - period_start).total_seconds() / 3600
            return total_hours, 0.0
        
        # Sort by timestamp
        sorted_records = sorted(state_records, key=lambda r: r.timestamp)
        
        uptime_seconds = 0
        current_state = 'stopped'  # Assume stopped initially
        last_timestamp = period_start
        
        for record in sorted_records:
            if current_state == 'started':
                # Add time since last state change
                uptime_seconds += (record.timestamp - last_timestamp).total_seconds()
            
            current_state = record.state
            last_timestamp = record.timestamp
        
        # Account for time until period end
        if current_state == 'started':
            uptime_seconds += (period_end - last_timestamp).total_seconds()
        
        total_seconds = (period_end - period_start).total_seconds()
        uptime_hours = uptime_seconds / 3600
        downtime_hours = (total_seconds - uptime_seconds) / 3600
        
        return uptime_hours, downtime_hours
    
    def _calculate_resource_cost(self, spec_records: List[SpecChangeRecord],
                                  resource_key: str,
                                  cost_per_unit_hour: float,
                                  period_start: datetime,
                                  period_end: datetime,
                                  state_records: List[StateChangeRecord]) -> float:
        """Calculate cost for a resource over the billing period."""
        if not spec_records:
            return 0.0
        
        # Sort records by timestamp
        sorted_records = sorted(spec_records, key=lambda r: r.timestamp)
        
        total_cost = 0.0
        last_record = sorted_records[0]
        last_timestamp = period_start
        
        for i, record in enumerate(sorted_records[1:], 1):
            hours = (record.timestamp - last_timestamp).total_seconds() / 3600
            resource_value = getattr(last_record, resource_key)
            total_cost += resource_value * cost_per_unit_hour * hours
            
            last_record = record
            last_timestamp = record.timestamp
        
        # Account for time until period end
        hours = (period_end - last_timestamp).total_seconds() / 3600
        resource_value = getattr(last_record, resource_key)
        total_cost += resource_value * cost_per_unit_hour * hours
        
        return total_cost
    
    def export_csv(self, report: BillingReport, 
                   output_path: Optional[str] = None) -> str:
        """
        Export a billing report to CSV format.
        
        Args:
            report: The billing report to export
            output_path: Optional custom output path
            
        Returns:
            Path to the generated CSV file
        """
        if output_path is None:
            filename = f"billing_{report.vm_id}_{report.period_start.strftime('%Y%m%d')}_{report.period_end.strftime('%Y%m%d')}.csv"
            output_path = os.path.join(self.csv_output_dir, filename)
        
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            
            # Write summary
            writer.writerow(['Billing Report'])
            writer.writerow(['VM ID', report.vm_id])
            writer.writerow(['VM Name', report.vm_name])
            writer.writerow(['Period Start', report.period_start.isoformat()])
            writer.writerow(['Period End', report.period_end.isoformat()])
            writer.writerow([])
            
            # Write resource statistics
            writer.writerow(['Resource Statistics'])
            writer.writerow(['Metric', 'Min', 'Max', 'Average'])
            writer.writerow(['CPU Cores', report.min_cpu_cores, report.max_cpu_cores, 
                           round(report.avg_cpu_cores, 2)])
            writer.writerow(['RAM (MB)', report.min_ram_mb, report.max_ram_mb,
                           round(report.avg_ram_mb, 2)])
            writer.writerow([])
            
            # Write uptime statistics
            writer.writerow(['Uptime Statistics'])
            writer.writerow(['Total Uptime (hours)', round(report.total_uptime_hours, 2)])
            writer.writerow(['Total Downtime (hours)', round(report.total_downtime_hours, 2)])
            writer.writerow(['Uptime Percentage', f"{round(report.uptime_percentage, 2)}%"])
            writer.writerow([])
            
            # Write cost
            writer.writerow(['Billing'])
            writer.writerow(['Total Cost', f"${round(report.total_cost, 4)}"])
            writer.writerow([])
            
            # Write spec changes
            if report.spec_changes:
                writer.writerow(['Spec Changes'])
                writer.writerow(['Timestamp', 'CPU Cores', 'RAM (MB)'])
                for change in report.spec_changes:
                    writer.writerow([change['timestamp'], change['cpu_cores'], change['ram_mb']])
        
        self.logger.info(f"Billing report exported to {output_path}")
        return output_path
    
    def run_webhook(self, report: BillingReport) -> None:
        """
        Run webhook script or POST to webhook URL with billing data.
        
        Args:
            report: The billing report to send
        """
        report_dict = report.to_dict()
        
        # Run webhook script if configured
        if self.webhook_script and os.path.exists(self.webhook_script):
            try:
                result = subprocess.run(
                    [self.webhook_script],
                    input=json.dumps(report_dict),
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode == 0:
                    self.logger.info(f"Webhook script executed successfully")
                else:
                    self.logger.error(f"Webhook script failed: {result.stderr}")
            except Exception as e:
                self.logger.error(f"Failed to run webhook script: {e}")
        
        # POST to webhook URL if configured
        if self.webhook_url:
            try:
                response = requests.post(
                    self.webhook_url,
                    json=report_dict,
                    timeout=30
                )
                response.raise_for_status()
                self.logger.info(f"Billing data posted to webhook URL successfully")
            except Exception as e:
                self.logger.error(f"Failed to POST to webhook URL: {e}")
    
    def generate_period_report(self, vm_id: str) -> Optional[BillingReport]:
        """
        Generate a billing report for the current billing period.
        
        Args:
            vm_id: The VM identifier
            
        Returns:
            BillingReport if successful, None otherwise
        """
        try:
            period_end = datetime.now()
            period_start = period_end - timedelta(days=self.billing_period_days)
            
            report = self.calculate_billing_period(vm_id, period_start, period_end)
            
            # Export to CSV
            csv_path = self.export_csv(report)
            
            # Run webhook if configured
            if self.webhook_script or self.webhook_url:
                self.run_webhook(report)
            
            return report
        except Exception as e:
            self.logger.error(f"Failed to generate billing report for VM {vm_id}: {e}")
            return None
