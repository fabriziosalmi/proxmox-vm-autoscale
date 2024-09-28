import paramiko
import logging
import os

class SSHClient:
    def __init__(self, host, user, password=None, key_path=None):
        """
        Initializes the SSH client with given credentials.
        :param host: Hostname or IP address of the server.
        :param user: Username to connect with.
        :param password: Password for SSH (optional).
        :param key_path: Path to the private SSH key (optional).
        """
        self.host = host
        self.user = user
        self.password = password
        self.key_path = key_path
        self.logger = logging.getLogger("ssh_utils")
        self.client = None

    def connect(self):
        """
        Establish an SSH connection to the host.
        """
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect using password or private key
            if self.password:
                self.client.connect(self.host, username=self.user, password=self.password, timeout=10)
            elif self.key_path:
                private_key = paramiko.RSAKey.from_private_key_file(self.key_path)
                self.client.connect(self.host, username=self.user, pkey=private_key, timeout=10)
            else:
                raise ValueError("Either password or key_path must be provided for SSH connection.")
            
            self.logger.info(f"Successfully connected to {self.host}")

        except Exception as e:
            self.logger.error(f"Failed to connect to {self.host}: {str(e)}")
            raise

    def execute_command(self, command):
        """
        Execute a command on the remote server.
        :param command: Command to execute.
        :return: Output of the command.
        """
        if not self.client:
            self.connect()

        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0:
                output = stdout.read().decode('utf-8')
                self.logger.info(f"Command executed successfully on {self.host}: {command}")
                return output
            else:
                error_message = stderr.read().decode('utf-8')
                self.logger.error(f"Command failed on {self.host}: {command}\nError: {error_message}")
                raise RuntimeError(f"Command execution failed: {error_message}")

        except Exception as e:
            self.logger.error(f"Error executing command on {self.host}: {str(e)}")
            raise

    def close(self):
        """
        Close the SSH connection.
        """
        if self.client:
            self.client.close()
            self.logger.info(f"SSH connection closed for {self.host}")

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
