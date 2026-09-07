"""
Billing Tracker Module for VM Autoscale

Tracks resource changes and calculates costs for billing web hosters.
Records CPU/RAM spec changes and VM state transitions, then generates
billing reports per period.
"""

import contextlib
import csv
import json
import logging
import os
import subprocess
import tempfile
import requests
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any
from pathlib import Path


class BillingDataError(RuntimeError):
    """The persisted billing history exists but could not be read."""


@dataclass
class SpecChangeRecord:
    """Record of a VM spec change."""
    timestamp: datetime
    cpu_cores: int
    ram_mb: int

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
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
    spec_changes: list[dict[str, Any]]
    total_cost: float

    def to_dict(self) -> dict[str, Any]:
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


def utcnow() -> datetime:
    """Timezone-aware current time.

    Records used naive `datetime.now()`, so any period spanning a DST
    transition was off by an hour with no way to detect it after the fact.
    """
    return datetime.now(timezone.utc)


def as_aware(value: datetime) -> datetime:
    """Attach UTC to a naive datetime, leaving aware ones untouched.

    Data written before timestamps became aware is naive on disk; treating it
    as UTC is the only interpretation that keeps old and new records
    comparable.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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

    def __init__(self, config: dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.billing_config = config.get('billing', {})

        # Storage for records (in production, this would be persisted to disk/DB)
        self._spec_changes: dict[str, list[SpecChangeRecord]] = {}
        self._state_changes: dict[str, list[StateChangeRecord]] = {}
        self._vm_names: dict[str, str] = {}
        # When the last period report was emitted, so the service knows when
        # the next one is due across restarts.
        self._last_report_time: datetime | None = None
        # Set when the on-disk history could not be read. Writes stay disabled
        # for the rest of the run so an empty tracker cannot clobber it.
        self._writes_disabled = False

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
        """Load persisted billing data from disk.

        A failure here is not recoverable by carrying on: the tracker would
        start empty and the next `_save_data` would overwrite the damaged file
        with that empty state, destroying the history permanently. Instead the
        damaged file is preserved under a `.corrupt-<timestamp>` name, writes
        are disabled, and the failure is raised to the caller.
        """
        data_file = self._get_data_file_path()
        if os.path.exists(data_file):
            try:
                with open(data_file) as f:
                    data = json.load(f)

                # Restore spec changes
                for vm_id, records in data.get('spec_changes', {}).items():
                    self._spec_changes[vm_id] = [
                        SpecChangeRecord(
                            timestamp=as_aware(datetime.fromisoformat(r['timestamp'])),
                            cpu_cores=r['cpu_cores'],
                            ram_mb=r['ram_mb']
                        ) for r in records
                    ]

                # Restore state changes
                for vm_id, records in data.get('state_changes', {}).items():
                    self._state_changes[vm_id] = [
                        StateChangeRecord(
                            timestamp=as_aware(datetime.fromisoformat(r['timestamp'])),
                            state=r['state']
                        ) for r in records
                    ]

                self._vm_names = data.get('vm_names', {})

                last_report = data.get('last_report_time')
                if last_report:
                    self._last_report_time = as_aware(datetime.fromisoformat(last_report))

                self.logger.debug(f"Loaded billing data from {data_file}")
            except Exception as e:
                quarantined = f"{data_file}.corrupt-{utcnow():%Y%m%dT%H%M%SZ}"
                try:
                    os.replace(data_file, quarantined)
                    kept = f" The unreadable file was kept as {quarantined}."
                except OSError as move_error:
                    quarantined = None
                    kept = f" It could not be moved aside either: {move_error}."

                self._writes_disabled = True
                self.logger.critical(
                    f"Billing data at {data_file} could not be read: {e}.{kept} "
                    "Billing writes are disabled for this run so the history is "
                    "not overwritten with empty state."
                )
                raise BillingDataError(
                    f"Unreadable billing data at {data_file}: {e}"
                ) from e

    def _save_data(self) -> None:
        """Persist billing data atomically.

        Written to a temporary file in the same directory, flushed, fsynced and
        renamed over the target, so a process killed mid-write leaves the
        previous file intact. The previous implementation opened the target
        with mode `w`, which truncates before the first byte is written - and
        since the service has no signal handling, being killed mid-write is the
        normal stop path, not an edge case.
        """
        if self._writes_disabled:
            self.logger.debug("Billing writes are disabled; skipping save.")
            return

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
                'vm_names': self._vm_names,
                'last_report_time': (
                    self._last_report_time.isoformat() if self._last_report_time else None
                ),
            }
            directory = os.path.dirname(data_file) or '.'
            fd, tmp_path = tempfile.mkstemp(
                dir=directory, prefix='.billing_data-', suffix='.tmp'
            )
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, data_file)
            except BaseException:
                # BaseException so a SystemExit or KeyboardInterrupt mid-write
                # still removes the partial file rather than leaving litter.
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise

            self.logger.debug(f"Saved billing data to {data_file}")
        except Exception as e:
            self.logger.error(f"Failed to save billing data: {e}")

    def get_last_report_time(self) -> datetime | None:
        """When the last period report was generated, or None if never."""
        return self._last_report_time

    def set_last_report_time(self, when: datetime | None = None) -> None:
        """Record that a period report has just been generated."""
        self._last_report_time = as_aware(when) if when else utcnow()
        self._save_data()

    def is_period_due(self, now: datetime | None = None) -> bool:
        """Whether a full billing period has elapsed since the last report.

        The first call starts the clock rather than emitting an empty report
        for a period the service was not running for.
        """
        now = as_aware(now) if now else utcnow()
        if self._last_report_time is None:
            self.set_last_report_time(now)
            return False
        return (now - self._last_report_time) >= timedelta(days=self.billing_period_days)

    def set_vm_name(self, vm_id: str, vm_name: str) -> None:
        """Set the human-readable name for a VM."""
        self._vm_names[str(vm_id)] = vm_name
        self._save_data()

    def record_spec_change(self, vm_id: str, cpu_cores: int, ram_mb: int,
                           timestamp: datetime | None = None) -> None:
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
            timestamp=as_aware(timestamp) if timestamp else utcnow(),
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
                                timestamp: datetime | None = None) -> None:
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
            timestamp=as_aware(timestamp) if timestamp else utcnow(),
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

        # Callers may hand in naive bounds; records on disk are UTC-aware.
        period_start = as_aware(period_start)
        period_end = as_aware(period_end)

        # Records that actually happened inside the period. These are what the
        # report lists, because they are the real events.
        spec_records = [
            r for r in self._spec_changes.get(vm_id, [])
            if period_start <= r.timestamp <= period_end
        ]

        # The series the period is *billed* from also carries in whatever was
        # in effect at period_start. Without it, a VM that never changed spec
        # during the period had no records at all and was billed zero.
        effective_specs = self._specs_in_effect(vm_id, period_start, period_end)
        state_records = self._states_in_effect(vm_id, period_start, period_end)

        # Calculate CPU/RAM statistics over the specs actually in effect
        if effective_specs:
            spec_records_for_stats = effective_specs
            cpu_values = [r.cpu_cores for r in spec_records_for_stats]
            ram_values = [r.ram_mb for r in spec_records_for_stats]
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
            effective_specs, 'cpu_cores', self.cost_per_cpu_hour,
            period_start, period_end, state_records
        )
        ram_cost = self._calculate_resource_cost(
            effective_specs, 'ram_mb', self.cost_per_gb_ram_hour / 1024,  # Convert to per-MB
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

    def _specs_in_effect(self, vm_id: str, period_start: datetime,
                         period_end: datetime) -> list[SpecChangeRecord]:
        """Spec records covering the period, including the one carried in.

        Spec changes are events, not samples: a VM that held one size for the
        whole period produced no record inside it. Filtering strictly to the
        period therefore billed such a VM zero. The last change before
        `period_start` is replayed as an opening record at `period_start`.
        """
        records = sorted(self._spec_changes.get(vm_id, []), key=lambda r: r.timestamp)
        inside = [r for r in records if period_start <= r.timestamp <= period_end]
        before = [r for r in records if r.timestamp < period_start]

        if not before:
            return inside

        carried = before[-1]
        opening = SpecChangeRecord(
            timestamp=period_start,
            cpu_cores=carried.cpu_cores,
            ram_mb=carried.ram_mb,
        )
        return [opening, *inside]

    def _states_in_effect(self, vm_id: str, period_start: datetime,
                          period_end: datetime) -> list[StateChangeRecord]:
        """State records for the period, including the state carried in.

        Without the carry-in, a VM that started months ago and was stopped once
        inside the period looked as though it had been down from `period_start`
        until that stop.
        """
        records = sorted(self._state_changes.get(vm_id, []), key=lambda r: r.timestamp)
        inside = [r for r in records if period_start <= r.timestamp <= period_end]
        before = [r for r in records if r.timestamp < period_start]

        if not before:
            return inside

        opening = StateChangeRecord(timestamp=period_start, state=before[-1].state)
        return [opening, *inside]

    def _uptime_intervals(self, state_records: list[StateChangeRecord],
                          period_start: datetime,
                          period_end: datetime) -> list[tuple]:
        """The (start, end) windows during which the VM was running."""
        if not state_records:
            # Nothing recorded: assume the VM was up for the whole period.
            return [(period_start, period_end)]

        sorted_records = sorted(state_records, key=lambda r: r.timestamp)
        intervals = []
        current_state = 'stopped'
        last_timestamp = period_start

        for record in sorted_records:
            if current_state == 'started' and record.timestamp > last_timestamp:
                intervals.append((last_timestamp, record.timestamp))
            current_state = record.state
            last_timestamp = record.timestamp

        if current_state == 'started' and period_end > last_timestamp:
            intervals.append((last_timestamp, period_end))

        return intervals

    def _calculate_uptime(self, state_records: list[StateChangeRecord],
                          period_start: datetime,
                          period_end: datetime) -> tuple:
        """Calculate total uptime and downtime hours for a period."""
        uptime_seconds = sum(
            (end - start).total_seconds()
            for start, end in self._uptime_intervals(state_records, period_start, period_end)
        )
        total_seconds = (period_end - period_start).total_seconds()
        return uptime_seconds / 3600, (total_seconds - uptime_seconds) / 3600

    @staticmethod
    def _overlap_seconds(a_start: datetime, a_end: datetime,
                         b_start: datetime, b_end: datetime) -> float:
        """Seconds shared by two intervals; zero when they do not overlap."""
        start = max(a_start, b_start)
        end = min(a_end, b_end)
        return max(0.0, (end - start).total_seconds())

    def _calculate_resource_cost(self, spec_records: list[SpecChangeRecord],
                                  resource_key: str,
                                  cost_per_unit_hour: float,
                                  period_start: datetime,
                                  period_end: datetime,
                                  state_records: list[StateChangeRecord]) -> float:
        """Cost for one resource over the period, charged only while the VM was up.

        `state_records` used to be accepted and ignored, so a VM powered off for
        a week was still billed for that week at its last known spec.
        """
        if not spec_records:
            return 0.0

        up_intervals = self._uptime_intervals(state_records, period_start, period_end)
        if not up_intervals:
            return 0.0

        sorted_records = sorted(spec_records, key=lambda r: r.timestamp)

        total_cost = 0.0
        last_record = sorted_records[0]
        last_timestamp = period_start

        def charge(spec, start, end):
            billable = sum(
                self._overlap_seconds(start, end, up_start, up_end)
                for up_start, up_end in up_intervals
            )
            return getattr(spec, resource_key) * cost_per_unit_hour * (billable / 3600)

        for record in sorted_records[1:]:
            total_cost += charge(last_record, last_timestamp, record.timestamp)
            last_record = record
            last_timestamp = record.timestamp

        total_cost += charge(last_record, last_timestamp, period_end)
        return total_cost

    def export_csv(self, report: BillingReport,
                   output_path: str | None = None) -> str:
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
        if self.webhook_script and self._webhook_script_is_safe():
            try:
                result = subprocess.run(
                    [self.webhook_script],
                    input=json.dumps(report_dict),
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode == 0:
                    self.logger.info("Webhook script executed successfully")
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
                self.logger.info("Billing data posted to webhook URL successfully")
            except Exception as e:
                self.logger.error(f"Failed to POST to webhook URL: {e}")

    def _webhook_script_is_safe(self) -> bool:
        """Refuse to execute a webhook script others can rewrite.

        This runs as root at every billing period boundary. A script that is
        group- or world-writable, or that lives in a directory anyone can
        write to, is an arbitrary root execution waiting for someone to notice
        it.
        """
        path = self.webhook_script
        if not os.path.exists(path):
            self.logger.error(f"Webhook script {path} does not exist; skipping it.")
            return False

        try:
            info = os.stat(path)
            directory = os.stat(os.path.dirname(os.path.abspath(path)))
        except OSError as e:
            self.logger.error(f"Could not inspect webhook script {path}: {e}")
            return False

        if not os.access(path, os.X_OK):
            self.logger.error(f"Webhook script {path} is not executable; skipping it.")
            return False

        for label, st in (("script", info), ("its directory", directory)):
            if st.st_mode & 0o022:
                self.logger.error(
                    f"Refusing to run {path}: {label} is writable by group or "
                    "others, and this runs as root."
                )
                return False
            if st.st_uid != os.getuid() and st.st_uid != 0:
                self.logger.error(
                    f"Refusing to run {path}: {label} is owned by uid "
                    f"{st.st_uid}, which is neither root nor this process."
                )
                return False

        return True

    def generate_period_report(self, vm_id: str) -> BillingReport | None:
        """
        Generate a billing report for the current billing period.

        Args:
            vm_id: The VM identifier

        Returns:
            BillingReport if successful, None otherwise
        """
        try:
            period_end = utcnow()
            period_start = period_end - timedelta(days=self.billing_period_days)

            report = self.calculate_billing_period(vm_id, period_start, period_end)

            # Export to CSV
            self.export_csv(report)

            # Run webhook if configured
            if self.webhook_script or self.webhook_url:
                self.run_webhook(report)

            return report
        except Exception as e:
            self.logger.error(f"Failed to generate billing report for VM {vm_id}: {e}")
            return None
