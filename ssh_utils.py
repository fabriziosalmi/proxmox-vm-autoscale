import logging
import os
import time

import paramiko
from paramiko.ssh_exception import (
    AuthenticationException,
    BadHostKeyException,
    SSHException,
)

DEFAULT_KNOWN_HOSTS = "/etc/vm_autoscale/known_hosts"

#: Host key policies, loosest last.
#:
#: ``accept-new``  trust a host the first time it is seen, record its key, and
#:                 refuse to connect if that key ever changes. This is what
#:                 ``ssh -o StrictHostKeyChecking=accept-new`` does.
#: ``strict``      only connect to hosts already present in known_hosts.
#: ``auto``        accept any key, every time, and never record it. This was
#:                 the previous behaviour and offers no protection at all.
HOST_KEY_POLICIES = ("accept-new", "strict", "auto")


class SSHClient:
    def __init__(self, host, user, password=None, key_path=None, port=22,
                 host_key_policy="accept-new", known_hosts=DEFAULT_KNOWN_HOSTS):
        """
        Initializes the SSH client with given credentials.
        :param host: Hostname or IP address of the server.
        :param user: Username to connect with.
        :param password: Password for SSH (optional).
        :param key_path: Path to the private SSH key (optional).
        :param port: Port for SSH connection (default: 22).
        :param host_key_policy: One of "accept-new", "strict" or "auto".
        :param known_hosts: Path to the known_hosts file used for verification.
        """
        self.host = host
        self.user = user
        self.password = password
        self.key_path = key_path
        self.port = port
        self.host_key_policy = (host_key_policy or "accept-new").lower()
        self.known_hosts = known_hosts
        self.logger = logging.getLogger("ssh_utils")
        self.client = None
        # Added max retries and backoff factor for connection attempts
        self.max_retries = 5
        self.backoff_factor = 1

        if self.host_key_policy not in HOST_KEY_POLICIES:
            raise ValueError(
                f"Unknown ssh_host_key_policy {host_key_policy!r}; "
                f"expected one of {', '.join(HOST_KEY_POLICIES)}"
            )

    def _apply_host_key_policy(self, client):
        """Configure host key verification on a fresh paramiko client.

        The previous implementation set AutoAddPolicy without ever loading or
        saving a known_hosts file, so every connection accepted whatever key it
        was offered and nothing was remembered between connections - no
        protection against an attacker sitting between the service and a node
        holding root credentials.
        """
        if self.host_key_policy == "auto":
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.logger.warning(
                f"Host key verification is disabled for {self.host} "
                "(ssh_host_key_policy: auto). Any key will be accepted."
            )
            return

        try:
            client.load_system_host_keys()
        except Exception as e:
            self.logger.debug(f"Could not load system host keys: {e}")

        if self.known_hosts:
            directory = os.path.dirname(self.known_hosts)
            try:
                if directory:
                    os.makedirs(directory, mode=0o700, exist_ok=True)
                if not os.path.exists(self.known_hosts):
                    # Create it so paramiko has somewhere to persist new keys.
                    with open(self.known_hosts, "a"):
                        pass
                    os.chmod(self.known_hosts, 0o600)
                client.load_host_keys(self.known_hosts)
            except OSError as e:
                self.logger.warning(
                    f"Could not use known_hosts file {self.known_hosts}: {e}. "
                    "Host keys learned during this run will not be remembered."
                )

        if self.host_key_policy == "strict":
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            # accept-new: record an unknown key, reject a changed one.
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self):
        """
        Establish an SSH connection to the host.
        """
        if self.client is not None and self.client.get_transport() and self.client.get_transport().is_active():
            self.logger.info(f"Already connected to {self.host}. Reusing the connection.")
            return

        attempt = 0
        while attempt < self.max_retries:
            try:
                self.client = paramiko.SSHClient()
                self._apply_host_key_policy(self.client)
                
                # Connect using password or private key
                if self.password:
                    self.client.connect(
                        hostname=self.host, 
                        username=self.user, 
                        password=self.password, 
                        port=self.port,
                        timeout=10
                    )
                elif self.key_path:
                    private_key = self._load_private_key()
                    self.client.connect(
                        hostname=self.host, 
                        username=self.user, 
                        pkey=private_key, 
                        port=self.port,
                        timeout=10
                    )
                else:
                    raise ValueError("Either password or key_path must be provided for SSH connection.")
                
                self.logger.info(f"Successfully connected to {self.host} on port {self.port}")
                break  # successful connection: exit loop

            except BadHostKeyException as e:
                self.logger.error(
                    f"Host key mismatch for {self.host}: the server presented a key "
                    f"that does not match the one recorded in {self.known_hosts}. "
                    "Refusing to connect. If the host was legitimately rebuilt, "
                    "remove its entry from that file; otherwise investigate before "
                    "doing anything else."
                )
                raise
            except AuthenticationException:
                self.logger.error(f"Authentication failed for {self.host}. Check credentials or key file.")
                raise
            except (SSHException, Exception) as e:
                attempt += 1
                if attempt >= self.max_retries:
                    self.logger.error(f"Failed to connect to {self.host} after {attempt} attempts.")
                    raise e
                sleep_time = self.backoff_factor * (2 ** (attempt - 1))
                self.logger.info(f"Retrying connection to {self.host} in {sleep_time} seconds (attempt {attempt}/{self.max_retries})")
                time.sleep(sleep_time)

    def _load_private_key(self):
        """Load the configured private key, whatever its type.

        `paramiko.PKey.from_path` detects the format itself, so Ed25519,
        ECDSA, RSA and DSS keys all work. The previous implementation called
        `RSAKey.from_private_key_file` directly, which meant an Ed25519 key -
        the type SECURITY.md recommends - simply failed to load.

        Older paramiko releases have no `from_path`, so fall back to trying
        each concrete key class in turn.
        """
        from_path = getattr(paramiko.PKey, "from_path", None)
        if from_path is not None:
            try:
                return from_path(self.key_path)
            except Exception as e:
                raise SSHException(
                    f"Could not load private key {self.key_path}: {e}. "
                    "Encrypted keys are not supported; use an unencrypted key "
                    "or an ssh-agent-independent copy."
                ) from e

        attempts = []
        for name in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey"):
            key_class = getattr(paramiko, name, None)
            if key_class is None:
                continue
            try:
                return key_class.from_private_key_file(self.key_path)
            except Exception as e:
                attempts.append(f"{name}: {e}")

        raise SSHException(
            f"Could not load private key {self.key_path} as any supported type. "
            + "; ".join(attempts)
        )

    def execute_command(self, command, timeout=30):
        """Execute a command on the remote server with retry logic."""
        attempts = 0
        while attempts < self.max_retries:
            try:
                # ...existing code before try...
                stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
                exit_status = stdout.channel.recv_exit_status()

                output = stdout.read().decode('utf-8').strip()
                error = stderr.read().decode('utf-8').strip()

                if exit_status == 0:
                    self.logger.info(f"Command executed successfully on {self.host}: {command}")
                    return output, error, exit_status
                else:
                    self.logger.warning(f"Command execution failed on {self.host} with exit status {exit_status}")
                    return output, error, exit_status
            except Exception as e:
                attempts += 1
                self.logger.error(f"Error executing command on {self.host} (attempt {attempts}): {str(e)}")
                self.close()
                try:
                    self.connect()
                except Exception as connect_err:
                    self.logger.error(f"Reconnection failed on {self.host}: {str(connect_err)}")
                time.sleep(self.backoff_factor * (2 ** (attempts - 1)))
        raise Exception(f"Failed to execute command on {self.host} after {attempts} attempts.")

    def close(self):
        """
        Close the SSH connection.
        """
        if self.client:
            try:
                self.client.close()
                self.logger.info(f"SSH connection closed for {self.host}")
            except Exception as e:
                self.logger.error(f"Error while closing SSH connection to {self.host}: {str(e)}")
            finally:
                self.client = None

    def __enter__(self):
        """
        Context manager entry.
        """
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Context manager exit - ensure the SSH connection is closed.
        """
        self.close()

    def is_connected(self):
        """
        Check if the SSH client is connected and transport is active.
        :return: True if connected, False otherwise.
        """
        return self.client is not None and self.client.get_transport() and self.client.get_transport().is_active()
