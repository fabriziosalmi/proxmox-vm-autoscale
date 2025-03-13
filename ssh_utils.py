import paramiko
import logging
import time
from paramiko.ssh_exception import SSHException, AuthenticationException

class SSHClient:
    def __init__(self, host, user, password=None, key_path=None, port=22):
        """
        Initializes the SSH client with given credentials.
        :param host: Hostname or IP address of the server.
        :param user: Username to connect with.
        :param password: Password for SSH (optional).
        :param key_path: Path to the private SSH key (optional).
        :param port: Port for SSH connection (default: 22).
        """
        self.host = host
        self.user = user
        self.password = password
        self.key_path = key_path
        self.port = port
        self.logger = logging.getLogger("ssh_utils")
        self.client = None
        # Added max retries and backoff factor for connection attempts
        self.max_retries = 5
        self.backoff_factor = 1

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
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                
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
                    private_key = paramiko.RSAKey.from_private_key_file(self.key_path)
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
