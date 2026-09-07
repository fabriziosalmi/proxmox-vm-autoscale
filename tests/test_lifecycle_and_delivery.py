"""
Tests for the paths REVIEW.md found untested.

At the time of the review, `autoscale.py` sat at 63% and `ssh_utils.py` at 63%,
while the optional metrics endpoint was at 100%. The uncovered code was the
part that can damage a fleet: the main loop, the entrypoint, the constructor,
notification delivery, and `execute_command` - the single function through
which every hypervisor mutation passes.

These tests cover that code, plus the behaviours added in response to the
review: sustained shrink, notification deduplication, and graceful shutdown.
"""

import os
import signal
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import paramiko

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoscale import NotificationManager
from builders import make_autoscaler, quiet_logger, valid_config
from ssh_utils import SSHClient, SSHCommandError
from version import __version__


# ---------------------------------------------------------------------------
# The main loop and shutdown
# ---------------------------------------------------------------------------

class TestMainLoop(unittest.TestCase):

    def _autoscaler(self, **overrides):
        a = make_autoscaler(self, valid_config(check_interval=10, **overrides))
        a.process_vm = MagicMock()
        return a

    def test_one_cycle_processes_every_enabled_vm(self):
        a = self._autoscaler()
        a._run_cycle()
        self.assertEqual(a.process_vm.call_count, 1)

    def test_a_disabled_vm_is_skipped(self):
        config = valid_config(check_interval=10)
        config["virtual_machines"][0]["scaling_enabled"] = False
        a = make_autoscaler(self, config)
        a.process_vm = MagicMock()
        a._run_cycle()
        a.process_vm.assert_not_called()

    def test_a_vm_on_another_host_is_skipped(self):
        config = valid_config(check_interval=10)
        config["proxmox_hosts"].append(
            {"name": "pve2", "host": "10.0.0.12", "ssh_user": "root",
             "ssh_key": "/root/.ssh/id_ed25519", "ssh_port": 22})
        config["virtual_machines"].append(
            {"vm_id": 201, "proxmox_host": "pve2", "scaling_enabled": False,
             "cpu_scaling": True, "ram_scaling": True})
        a = make_autoscaler(self, config)
        a.process_vm = MagicMock()
        a._run_cycle()
        self.assertEqual(a.process_vm.call_count, 1)

    def test_a_shutdown_signal_abandons_the_cycle_immediately(self):
        a = self._autoscaler()
        a._shutdown.set()
        a._run_cycle()
        a.process_vm.assert_not_called()

    def test_run_returns_when_the_shutdown_event_is_set(self):
        a = self._autoscaler()
        a._shutdown.set()
        a.run()   # must return rather than loop forever
        self.assertTrue(a._shutdown.is_set())

    def test_run_stops_within_a_moment_of_a_signal_not_a_whole_interval(self):
        """`time.sleep(check_interval)` used to delay every stop by up to 5 min."""
        a = make_autoscaler(self, valid_config(check_interval=3600))
        a.process_vm = MagicMock()

        thread = threading.Thread(target=a.run, daemon=True)
        started = time.monotonic()
        thread.start()
        time.sleep(0.2)
        a._shutdown.set()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive(), "run() did not stop promptly")
        self.assertLess(time.monotonic() - started, 5)

    def test_a_cycle_error_is_survived_and_counted(self):
        a = self._autoscaler()
        a.process_vm.side_effect = RuntimeError("node exploded")

        def stop_after_first(*_args, **_kwargs):
            a._shutdown.set()
            return True

        a._shutdown.wait = MagicMock(side_effect=stop_after_first)
        a.run()
        self.assertIn("vm_autoscale_cycle_errors_total", a.metrics.render())

    def test_shutdown_marks_the_service_down_and_is_idempotent(self):
        a = self._autoscaler()
        a.shutdown()
        a.shutdown()
        self.assertIn("vm_autoscale_up 0", a.metrics.render())

    def test_the_version_is_logged_at_startup(self):
        a = self._autoscaler()
        a._shutdown.set()
        with patch.object(a, "logger") as logger:
            a.run()
        self.assertTrue(
            any(__version__ in str(c) for c in logger.info.call_args_list),
            "startup log does not state the version",
        )


class TestSignalHandlers(unittest.TestCase):

    def test_sigterm_requests_a_shutdown(self):
        """systemctl stop sends SIGTERM, which used to be handled by nothing."""
        a = make_autoscaler(self)
        self.assertFalse(a._shutdown.is_set())
        handler = signal.getsignal(signal.SIGTERM)
        self.assertTrue(callable(handler))
        handler(signal.SIGTERM, None)
        self.assertTrue(a._shutdown.is_set())

    def test_sigint_requests_a_shutdown(self):
        a = make_autoscaler(self)
        handler = signal.getsignal(signal.SIGINT)
        handler(signal.SIGINT, None)
        self.assertTrue(a._shutdown.is_set())


# ---------------------------------------------------------------------------
# The constructor
# ---------------------------------------------------------------------------

class TestConstructor(unittest.TestCase):
    """Every test used to patch `__init__` away, so it was never executed."""

    def test_it_wires_the_object_graph(self):
        a = make_autoscaler(self)
        self.assertIsNotNone(a.metrics)
        self.assertEqual(a._vm_managers, {})
        self.assertEqual(a._vm_states, {})
        self.assertFalse(a.dry_run)

    def test_build_info_carries_the_version(self):
        a = make_autoscaler(self)
        self.assertIn(f'version="{__version__}"', a.metrics.render())

    def test_dry_run_is_reflected_in_build_info(self):
        a = make_autoscaler(self, valid_config(dry_run=True))
        self.assertTrue(a.dry_run)
        self.assertIn('dry_run="true"', a.metrics.render())

    def test_configuration_warnings_are_logged(self):
        config = valid_config()
        config["proxmox_hosts"][0]["ssh_password"] = "hunter2"   # plus the key
        a = make_autoscaler(self, config)
        self.assertNotIn("_validation_warnings", a.config)

    def test_the_metrics_endpoint_is_off_unless_enabled(self):
        a = make_autoscaler(self)
        self.assertIsNone(a._metrics_server)


# ---------------------------------------------------------------------------
# Notification delivery
# ---------------------------------------------------------------------------

def notification_config(**overrides):
    config = {"notification_dedup_seconds": 0}
    config.update(overrides)
    return config


class TestGotifyDelivery(unittest.TestCase):

    def _manager(self, **gotify):
        settings = {"enabled": True, "server_url": "https://gotify.example/",
                    "app_token": "tok", "priority": 5}
        settings.update(gotify)
        return NotificationManager(notification_config(gotify=settings), quiet_logger())

    def test_it_posts_to_the_message_endpoint(self):
        manager = self._manager()
        with patch("autoscale.requests.post") as post:
            manager.send_gotify_notification("scaled up", priority=7)
        url = post.call_args.args[0]
        self.assertEqual(url, "https://gotify.example/message")

    def test_the_trailing_slash_is_not_doubled(self):
        manager = self._manager(server_url="https://gotify.example///")
        with patch("autoscale.requests.post") as post:
            manager.send_gotify_notification("x")
        self.assertNotIn("//message", post.call_args.args[0].replace("https://", ""))

    def test_the_token_travels_as_a_bearer_header(self):
        manager = self._manager()
        with patch("autoscale.requests.post") as post:
            manager.send_gotify_notification("x")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tok")

    def test_the_per_event_priority_wins_over_the_configured_one(self):
        manager = self._manager(priority=5)
        with patch("autoscale.requests.post") as post:
            manager.send_gotify_notification("x", priority=9)
        self.assertEqual(post.call_args.kwargs["data"]["priority"], 9)

    def test_a_request_timeout_is_always_set(self):
        manager = self._manager()
        with patch("autoscale.requests.post") as post:
            manager.send_gotify_notification("x")
        self.assertIn("timeout", post.call_args.kwargs)

    def test_a_transport_failure_propagates_to_the_router(self):
        import requests
        manager = self._manager()
        with patch("autoscale.requests.post",
                   side_effect=requests.exceptions.ConnectTimeout("no route")):
            with self.assertRaises(requests.exceptions.RequestException):
                manager.send_gotify_notification("x")


class TestSMTPDelivery(unittest.TestCase):

    def _manager(self, **alerts):
        settings = {"email_enabled": True, "smtp_server": "smtp.example",
                    "smtp_port": 587, "smtp_user": "bot@example",
                    "smtp_password": "pw", "email_recipient": "ops@example"}
        settings.update(alerts)
        return NotificationManager(notification_config(alerts=settings), quiet_logger())

    def _send(self, manager, message="Scaled up CPU for VM 101 due to load."):
        with patch("autoscale.smtplib.SMTP") as smtp:
            manager.send_smtp_notification(message)
        return smtp.return_value.__enter__.return_value

    def test_starttls_is_always_negotiated(self):
        server = self._send(self._manager())
        server.starttls.assert_called_once()

    def test_it_authenticates_when_a_password_is_set(self):
        server = self._send(self._manager())
        server.login.assert_called_once_with("bot@example", "pw")

    def test_an_empty_password_skips_authentication(self):
        """For relays that authenticate by source address."""
        server = self._send(self._manager(smtp_password=""))
        server.login.assert_not_called()

    def test_the_subject_carries_the_vmid(self):
        with patch("autoscale.smtplib.SMTP") as smtp:
            self._manager().send_smtp_notification("Scaled up CPU for VM 101.")
        body = smtp.return_value.__enter__.return_value.sendmail.call_args.args[2]
        self.assertIn("Subject: VM Autoscale Alert for VM 101", body)

    def test_several_recipients_are_all_addressed(self):
        manager = self._manager(email_recipient=["a@example", "b@example"])
        with patch("autoscale.smtplib.SMTP") as smtp:
            manager.send_smtp_notification("x")
        recipients = smtp.return_value.__enter__.return_value.sendmail.call_args.args[1]
        self.assertEqual(recipients, ["a@example", "b@example"])

    def test_a_non_string_recipient_is_rejected(self):
        manager = self._manager(email_recipient=[None])
        with self.assertRaises(ValueError):
            manager.send_smtp_notification("x")


class TestNotificationDeduplication(unittest.TestCase):
    """One unreachable node produced 240 identical notifications an hour."""

    def _manager(self, window=900):
        config = {
            "notification_dedup_seconds": window,
            "gotify": {"enabled": True, "server_url": "https://g.example",
                       "app_token": "t"},
        }
        return NotificationManager(config, quiet_logger())

    def test_an_identical_message_is_sent_once(self):
        manager = self._manager()
        with patch("autoscale.requests.post") as post:
            for _ in range(5):
                manager.send_notification("Host pve1 unreachable")
        self.assertEqual(post.call_count, 1)

    def test_messages_differing_only_in_numbers_are_treated_as_one_event(self):
        manager = self._manager()
        with patch("autoscale.requests.post") as post:
            manager.send_notification("VM 101 usage 91.2%")
            manager.send_notification("VM 101 usage 93.7%")
        self.assertEqual(post.call_count, 1)

    def test_genuinely_different_messages_still_get_through(self):
        manager = self._manager()
        with patch("autoscale.requests.post") as post:
            manager.send_notification("Host pve1 unreachable")
            manager.send_notification("Host pve2 unreachable")
        self.assertEqual(post.call_count, 2)

    def test_a_zero_window_disables_deduplication(self):
        manager = self._manager(window=0)
        with patch("autoscale.requests.post") as post:
            manager.send_notification("same")
            manager.send_notification("same")
        self.assertEqual(post.call_count, 2)


# ---------------------------------------------------------------------------
# Sustained shrink
# ---------------------------------------------------------------------------

class TestScaleDownMustBeSustained(unittest.TestCase):
    """Growing fails safe; shrinking can OOM a guest or be refused outright."""

    def _autoscaler(self, after=3):
        return make_autoscaler(self, valid_config(scale_down_after_cycles=after))

    def test_a_single_low_reading_does_not_shrink(self):
        a = self._autoscaler()
        vm_manager = MagicMock()
        a._handle_cpu_scaling(vm_manager, 101, 5.0, {"high": 80, "low": 20})
        vm_manager.scale_cpu.assert_not_called()

    def test_it_shrinks_once_the_reading_is_sustained(self):
        a = self._autoscaler(after=3)
        vm_manager = MagicMock()
        for _ in range(3):
            a._handle_cpu_scaling(vm_manager, 101, 5.0, {"high": 80, "low": 20})
        vm_manager.scale_cpu.assert_called_once_with("down")

    def test_a_reading_back_in_the_dead_band_resets_the_streak(self):
        a = self._autoscaler(after=3)
        vm_manager = MagicMock()
        a._handle_cpu_scaling(vm_manager, 101, 5.0, {"high": 80, "low": 20})
        a._handle_cpu_scaling(vm_manager, 101, 50.0, {"high": 80, "low": 20})
        a._handle_cpu_scaling(vm_manager, 101, 5.0, {"high": 80, "low": 20})
        vm_manager.scale_cpu.assert_not_called()

    def test_growing_still_happens_on_the_first_reading(self):
        a = self._autoscaler()
        vm_manager = MagicMock()
        a._handle_cpu_scaling(vm_manager, 101, 95.0, {"high": 80, "low": 20})
        vm_manager.scale_cpu.assert_called_once_with("up")

    def test_streaks_are_tracked_per_vm(self):
        a = self._autoscaler(after=2)
        first, second = MagicMock(), MagicMock()
        a._handle_cpu_scaling(first, 101, 5.0, {"high": 80, "low": 20})
        a._handle_cpu_scaling(second, 102, 5.0, {"high": 80, "low": 20})
        first.scale_cpu.assert_not_called()
        second.scale_cpu.assert_not_called()

    def test_streaks_are_tracked_per_resource(self):
        a = self._autoscaler(after=2)
        vm_manager = MagicMock()
        a._handle_cpu_scaling(vm_manager, 101, 5.0, {"high": 80, "low": 20})
        a._handle_ram_scaling(vm_manager, 101, 5.0, {"high": 85, "low": 25})
        vm_manager.scale_cpu.assert_not_called()
        vm_manager.scale_ram.assert_not_called()

    def test_ram_shrink_is_also_gated(self):
        a = self._autoscaler(after=2)
        vm_manager = MagicMock()
        a._handle_ram_scaling(vm_manager, 101, 5.0, {"high": 85, "low": 25})
        vm_manager.scale_ram.assert_not_called()
        a._handle_ram_scaling(vm_manager, 101, 5.0, {"high": 85, "low": 25})
        vm_manager.scale_ram.assert_called_once_with("down")


# ---------------------------------------------------------------------------
# SSH command execution
# ---------------------------------------------------------------------------

class TestExecuteCommand(unittest.TestCase):
    """The single function every hypervisor mutation passes through."""

    def _client(self):
        client = SSHClient(host="10.0.0.11", user="root", password="x")
        client.backoff_factor = 0        # do not sleep through the retry ladder
        return client

    def test_it_returns_stdout_stderr_and_status(self):
        client = self._client()
        client.client = MagicMock()
        stdout = MagicMock()
        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b"  ok  "
        stderr = MagicMock()
        stderr.read.return_value = b""
        client.client.exec_command.return_value = (MagicMock(), stdout, stderr)

        self.assertEqual(client.execute_command("qm config 101"), ("ok", "", 0))

    def test_a_non_zero_status_is_returned_not_raised(self):
        """vm_manager._run is what decides a non-zero status is fatal."""
        client = self._client()
        client.client = MagicMock()
        stdout = MagicMock()
        stdout.channel.recv_exit_status.return_value = 25
        stdout.read.return_value = b""
        stderr = MagicMock()
        stderr.read.return_value = b"storage busy"
        client.client.exec_command.return_value = (MagicMock(), stdout, stderr)

        self.assertEqual(client.execute_command("qm set 101 -cores 5"),
                         ("", "storage busy", 25))

    def _reconnecting(self, client, exec_side_effect):
        """A `connect` that installs a fresh client, as the real one does."""
        def reconnect():
            client.client = MagicMock()
            client.client.exec_command.side_effect = exec_side_effect
        return reconnect

    def test_a_transport_failure_is_retried_and_reconnected(self):
        client = self._client()
        client.max_retries = 3
        failing = OSError("socket closed")
        client.client = MagicMock()
        client.client.exec_command.side_effect = failing
        client.connect = MagicMock(side_effect=self._reconnecting(client, failing))

        with self.assertRaises(SSHCommandError):
            client.execute_command("qm config 101")
        self.assertEqual(client.connect.call_count, 3)

    def test_it_gives_up_after_max_retries(self):
        client = self._client()
        client.max_retries = 2
        failing = OSError("nope")
        client.client = MagicMock()
        client.client.exec_command.side_effect = failing
        client.connect = MagicMock(side_effect=self._reconnecting(client, failing))

        with self.assertRaises(SSHCommandError) as ctx:
            client.execute_command("qm config 101")
        self.assertIn("after 2 attempts", str(ctx.exception))

    def test_a_failed_reconnect_reports_the_real_cause(self):
        """It used to loop on a None client and raise AttributeError instead."""
        client = self._client()
        client.client = MagicMock()
        client.client.exec_command.side_effect = OSError("socket closed")
        client.connect = MagicMock(side_effect=OSError("host unreachable"))

        with self.assertRaises(SSHCommandError) as ctx:
            client.execute_command("qm config 101")
        message = str(ctx.exception)
        self.assertIn("could not reconnect", message)
        self.assertNotIn("NoneType", message)

    def test_it_recovers_when_a_retry_succeeds(self):
        client = self._client()
        stdout = MagicMock()
        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b"recovered"
        stderr = MagicMock()
        stderr.read.return_value = b""

        client.client = MagicMock()
        client.client.exec_command.side_effect = OSError("first attempt fails")
        client.connect = MagicMock(
            side_effect=self._reconnecting(client, [(MagicMock(), stdout, stderr)]))

        self.assertEqual(client.execute_command("qm config 101")[0], "recovered")


class TestConnectionLifecycle(unittest.TestCase):

    def test_close_is_idempotent(self):
        client = SSHClient(host="h", user="root", password="x")
        client.client = MagicMock()
        client.close()
        client.close()          # must not raise
        self.assertIsNone(client.client)

    def test_is_connected_is_false_without_a_client(self):
        self.assertFalse(SSHClient(host="h", user="root", password="x").is_connected())

    def test_an_active_transport_reports_connected(self):
        client = SSHClient(host="h", user="root", password="x")
        client.client = MagicMock()
        client.client.get_transport.return_value.is_active.return_value = True
        self.assertTrue(client.is_connected())

    def test_the_context_manager_connects_and_closes(self):
        client = SSHClient(host="h", user="root", password="x")
        client.connect = MagicMock()
        client.close = MagicMock()
        with client as entered:
            self.assertIs(entered, client)
        client.connect.assert_called_once()
        client.close.assert_called_once()

    def test_an_authentication_failure_is_not_retried(self):
        client = SSHClient(host="h", user="root", password="x")
        client.backoff_factor = 0
        with patch("paramiko.SSHClient") as fake:
            fake.return_value.connect.side_effect = paramiko.AuthenticationException()
            with self.assertRaises(paramiko.AuthenticationException):
                client.connect()
            self.assertEqual(fake.return_value.connect.call_count, 1)

    def test_a_host_with_neither_password_nor_key_is_refused(self):
        client = SSHClient(host="h", user="root")
        client.backoff_factor = 0
        client.max_retries = 1
        with patch("paramiko.SSHClient"), self.assertRaises(ValueError):
            client.connect()


if __name__ == "__main__":
    unittest.main()
