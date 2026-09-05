"""
Regression tests for private key loading.

`SSHClient` used to call `paramiko.RSAKey.from_private_key_file` directly, so
an Ed25519 key - the type `SECURITY.md` recommends - failed to load however
valid it was, and the connection fell through to failing. These tests generate
real keys of each type on disk and load them through the production path.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import paramiko

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssh_utils import SSHClient

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519

    HAVE_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover - cryptography ships with paramiko
    HAVE_CRYPTOGRAPHY = False


def write_openssh_key(private_key, path):
    """Serialise a `cryptography` key to an unencrypted OpenSSH file."""
    data = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)
    return path


def client_for(key_path):
    return SSHClient(host="10.0.0.11", user="root", key_path=key_path)


class TestPrivateKeyLoading(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography not available")
    def test_loads_an_ed25519_key(self):
        """The case that used to fail outright."""
        p = write_openssh_key(ed25519.Ed25519PrivateKey.generate(), self.path("id_ed25519"))
        key = client_for(p)._load_private_key()
        self.assertIsInstance(key, paramiko.PKey)
        self.assertIn("ed25519", key.get_name().lower())

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography not available")
    def test_loads_an_ecdsa_key(self):
        p = write_openssh_key(
            ec.generate_private_key(ec.SECP256R1()), self.path("id_ecdsa")
        )
        key = client_for(p)._load_private_key()
        self.assertIsInstance(key, paramiko.PKey)
        self.assertIn("ecdsa", key.get_name().lower())

    def test_still_loads_an_rsa_key(self):
        """The previously supported type must keep working."""
        p = self.path("id_rsa")
        paramiko.RSAKey.generate(2048).write_private_key_file(p)
        key = client_for(p)._load_private_key()
        self.assertIsInstance(key, paramiko.PKey)
        self.assertIn("rsa", key.get_name().lower())

    def test_garbage_file_raises_with_the_path_in_the_message(self):
        p = self.path("not_a_key")
        with open(p, "w") as fh:
            fh.write("this is not a key\n")
        with self.assertRaises(paramiko.ssh_exception.SSHException) as ctx:
            client_for(p)._load_private_key()
        self.assertIn(p, str(ctx.exception))

    def test_missing_file_raises(self):
        with self.assertRaises(Exception):
            client_for(self.path("absent"))._load_private_key()

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography not available")
    def test_fallback_path_loads_ed25519_without_pkey_from_path(self):
        """Older paramiko has no PKey.from_path; the per-class loop covers it."""
        p = write_openssh_key(ed25519.Ed25519PrivateKey.generate(), self.path("id_ed25519"))
        with patch.object(paramiko.PKey, "from_path", None, create=True):
            key = client_for(p)._load_private_key()
        self.assertIn("ed25519", key.get_name().lower())

    def test_fallback_path_reports_every_attempt_when_all_fail(self):
        p = self.path("not_a_key")
        with open(p, "w") as fh:
            fh.write("nope\n")
        with patch.object(paramiko.PKey, "from_path", None, create=True):
            with self.assertRaises(paramiko.ssh_exception.SSHException) as ctx:
                client_for(p)._load_private_key()
        message = str(ctx.exception)
        self.assertIn("RSAKey", message)
        self.assertIn("Ed25519Key", message)


class TestConnectUsesTheLoadedKey(unittest.TestCase):

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography not available")
    def test_connect_passes_an_ed25519_pkey_to_paramiko(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = write_openssh_key(
            ed25519.Ed25519PrivateKey.generate(), os.path.join(tmp.name, "id_ed25519")
        )

        client = client_for(p)
        with patch("paramiko.SSHClient") as fake_ssh_client:
            client.connect()

        instance = fake_ssh_client.return_value
        instance.connect.assert_called_once()
        pkey = instance.connect.call_args.kwargs["pkey"]
        self.assertIn("ed25519", pkey.get_name().lower())
        self.assertIsNone(instance.connect.call_args.kwargs.get("password"))


if __name__ == "__main__":
    unittest.main()
