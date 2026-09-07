"""
Configuration and safety rails for the end-to-end suite.

These tests drive a **real Proxmox node as root** and resize a **real VM**. The
whole point is to exercise what mocks cannot, which means everything here can
change someone's infrastructure. The guards below exist because the cost of
pointing this at the wrong VMID is somebody's production guest.

Nothing is configured in the repository. The node address, the credentials and
the VMID all come from the environment, so a private address never reaches a
public commit:

    export VMA_E2E_HOST=10.0.0.11
    export VMA_E2E_KEY=~/.ssh/id_rsa
    export VMA_E2E_VMID=9001
    pytest -m e2e -v

Without `VMA_E2E_HOST` the suite skips. A *partial* configuration does not skip
— it fails, loudly, naming what is missing. Silently skipping on a typo is the
exact class of defect this project has spent several releases removing.
"""

import os
import time
import unittest

from ssh_utils import SSHClient

#: A VM is only eligible if its name says so. Pointing the suite at a
#: production guest by mistyping a VMID would otherwise resize it.
REQUIRED_NAME_MARKER = "testbed"

#: What the VM is put back to before and after every test, so runs are
#: repeatable and the node is not left drifted. The core/vCPU split is
#: deliberate: it gives headroom so the live vCPU hotplug path is exercised
#: rather than the reboot-required one.
#:
#: `hotplug` and `numa` are here rather than assumed. A run where they had
#: silently gone missing from the testbed produced five failures that all
#: looked like scaling bugs and were not: the suite must own every input its
#: assertions depend on, or it reports on the node's mood.
BASELINE = {
    "cores": 4,
    "vcpus": 2,
    "memory": 2048,
    "balloon": 2048,
    "hotplug": "cpu,memory,network,disk,usb",
    "numa": 1,
}

#: Settings that are safe to write in any order, unlike the memory pair.
_ORDER_INSENSITIVE = ("cores", "vcpus", "hotplug", "numa")

_REQUIRED = ("VMA_E2E_HOST",)
_OPTIONAL_WITH_DEFAULTS = {
    "VMA_E2E_USER": "root",
    "VMA_E2E_PORT": "22",
    "VMA_E2E_VMID": "9001",
}


class E2EMisconfigured(RuntimeError):
    """The suite was asked to run but cannot be run safely."""


def configured() -> bool:
    """Whether the operator has asked for the end-to-end suite at all."""
    return bool(os.environ.get("VMA_E2E_HOST"))


def settings() -> dict:
    """Resolve the environment, refusing a half-configured run."""
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        raise E2EMisconfigured(f"missing environment: {', '.join(missing)}")

    key_path = os.environ.get("VMA_E2E_KEY")
    password = os.environ.get("VMA_E2E_PASSWORD")
    if not key_path and not password:
        raise E2EMisconfigured(
            "set VMA_E2E_KEY (a private key path) or VMA_E2E_PASSWORD"
        )
    if key_path:
        key_path = os.path.expanduser(key_path)
        if not os.path.exists(key_path):
            raise E2EMisconfigured(f"VMA_E2E_KEY does not exist: {key_path}")

    resolved = {name: os.environ.get(name, default)
                for name, default in _OPTIONAL_WITH_DEFAULTS.items()}

    vmid = resolved["VMA_E2E_VMID"]
    if not vmid.isdigit():
        raise E2EMisconfigured(f"VMA_E2E_VMID must be numeric, got {vmid!r}")

    return {
        "host": os.environ["VMA_E2E_HOST"],
        "user": resolved["VMA_E2E_USER"],
        "port": int(resolved["VMA_E2E_PORT"]),
        "vmid": vmid,
        "key_path": key_path,
        "password": password,
        "known_hosts": os.environ.get(
            "VMA_E2E_KNOWN_HOSTS",
            os.path.join(os.path.expanduser("~"), ".ssh", "known_hosts"),
        ),
    }


def connect(config: dict) -> SSHClient:
    """Open a connection using the project's own SSH client, not a shortcut."""
    client = SSHClient(
        host=config["host"],
        user=config["user"],
        port=config["port"],
        key_path=config["key_path"],
        password=config["password"],
        host_key_policy="accept-new",
        known_hosts=config["known_hosts"],
    )
    client.connect()
    return client


def run(client: SSHClient, command: str, check: bool = True) -> str:
    """Run a command on the node and return stdout.

    The node may print warnings to stderr on a successful call — a stale ACL
    token entry does exactly that — so success is judged by the exit status,
    which is the defect this project shipped a fix for in v1.7.1.
    """
    result = client.execute_command(command)
    output, error, status = (result if isinstance(result, tuple)
                             else (result, "", 0))
    if check and status != 0:
        raise AssertionError(
            f"`{command}` failed with exit status {status}"
            f"{': ' + error.strip() if error else ''}"
        )
    return output.strip()


def vm_config(client: SSHClient, vmid: str) -> dict:
    """`qm config <vmid>` as a mapping."""
    parsed = {}
    for line in run(client, f"qm config {vmid}").splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            parsed[key.strip()] = value.strip()
    return parsed


def vm_status(client: SSHClient, vmid: str) -> dict:
    """`qm status <vmid> --verbose` as a mapping — QEMU's live view."""
    parsed = {}
    for line in run(client, f"qm status {vmid} --verbose").splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            parsed[key.strip()] = value.strip()
    return parsed


def assert_is_a_testbed(client: SSHClient, vmid: str) -> None:
    """Refuse to touch a VM that is not explicitly marked as disposable.

    A mistyped VMID would otherwise resize whatever happens to be there.
    """
    try:
        config = vm_config(client, vmid)
    except AssertionError as e:
        raise E2EMisconfigured(
            f"VM {vmid} could not be read on {client.host}: {e}"
        ) from e

    name = config.get("name", "")
    if REQUIRED_NAME_MARKER not in name.lower():
        raise E2EMisconfigured(
            f"refusing to run against VM {vmid} on {client.host}: its name is "
            f"{name!r} and does not contain {REQUIRED_NAME_MARKER!r}. Point "
            "VMA_E2E_VMID at a disposable VM whose name says so."
        )


def restore_baseline(client: SSHClient, vmid: str) -> None:
    """Put the testbed back, so a run leaves nothing behind and repeats.

    `memory` and `balloon` cannot be written in an arbitrary order: Proxmox
    rejects a balloon above its ceiling *and* a ceiling below its balloon, so
    whichever is moving down goes second. Lowering the ceiling of a running
    guest also unplugs a DIMM, which the guest may refuse — the value is
    written regardless, so the refusal is tolerated here rather than failing
    an unrelated test in teardown.
    """
    fixed = " ".join(f"--{key} {BASELINE[key]}" for key in _ORDER_INSENSITIVE)
    run(client, f"qm set {vmid} {fixed}")

    ceiling = int(vm_config(client, vmid).get("memory", 512))
    if ceiling < BASELINE["memory"]:
        run(client, f"qm set {vmid} --memory {BASELINE['memory']} "
                    f"--balloon {BASELINE['balloon']}")
    else:
        run(client, f"qm set {vmid} --balloon {BASELINE['balloon']}")
        run(client, f"qm set {vmid} --memory {BASELINE['memory']}", check=False)

    assert_at_baseline(client, vmid)


def assert_at_baseline(client: SSHClient, vmid: str) -> None:
    """Fail here, not three tests later, if the testbed is not what we assume.

    Every assertion downstream reads as a scaling bug when the input drifted.
    """
    config = vm_config(client, vmid)
    drifted = {
        key: config.get(key)
        for key, expected in BASELINE.items()
        if str(config.get(key)) != str(expected)
    }
    if drifted:
        raise AssertionError(
            f"VM {vmid} is not at the baseline after a restore: {drifted} "
            f"(expected {BASELINE}). The tests measure the wrong thing until "
            "this is fixed."
        )


def ensure_running(client: SSHClient, vmid: str, timeout: int = 60) -> None:
    """Start the guest if it is not up, and wait for QEMU to report it."""
    if vm_status(client, vmid).get("status") == "running":
        return

    run(client, f"qm start {vmid}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if vm_status(client, vmid).get("status") == "running":
            return
        time.sleep(2)
    raise AssertionError(f"VM {vmid} did not reach running within {timeout}s")


def wait_for(predicate, timeout: float = 20, interval: float = 1.0):
    """Poll until `predicate` returns something truthy, or give up.

    Hotplug is asynchronous: QEMU acknowledges the command before the guest has
    finished acting on it. Asserting immediately would make the suite flaky in
    the direction that hides real failures.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last


class E2ETestCase(unittest.TestCase):
    """Base class: one connection per class, baseline restored around each test."""

    client = None
    config = None
    #: Whether the guest was already up when the class started. A run must not
    #: leave someone's node with a VM powered on that they had stopped.
    _was_running = None

    @classmethod
    def setUpClass(cls):
        if not configured():
            raise unittest.SkipTest(
                "end-to-end suite not configured; set VMA_E2E_HOST to run it "
                "(see tests/e2e_support.py)"
            )
        cls.config = settings()
        cls.client = connect(cls.config)
        cls.vmid = cls.config["vmid"]
        assert_is_a_testbed(cls.client, cls.vmid)
        cls._was_running = vm_status(cls.client, cls.vmid).get("status") == "running"

    @classmethod
    def ensure_guest_running(cls):
        """Start the guest for tests that need one, remembering to undo it."""
        ensure_running(cls.client, cls.vmid)

    @classmethod
    def tearDownClass(cls):
        if cls.client is None:
            return
        try:
            restore_baseline(cls.client, cls.vmid)
            running_now = vm_status(cls.client, cls.vmid).get("status") == "running"
            if running_now and not cls._was_running:
                run(cls.client, f"qm stop {cls.vmid}", check=False)
        finally:
            cls.client.close()

    def setUp(self):
        restore_baseline(self.client, self.vmid)
        self.addCleanup(restore_baseline, self.client, self.vmid)

    # -- convenience ------------------------------------------------------

    def run_command(self, command, check=True):
        return run(self.client, command, check=check)

    def config_of(self, vmid=None):
        return vm_config(self.client, vmid or self.vmid)

    def status_of(self, vmid=None):
        return vm_status(self.client, vmid or self.vmid)
