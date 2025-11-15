# Security Policy

## Supported Versions

This project is currently in active development. We recommend using the latest version from the `main` branch.

| Version | Supported          |
| ------- | ------------------ |
| Latest (main) | :white_check_mark: |
| < 1.0   | :warning: Alpha - Use with caution |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue, please follow these steps:

### 🔒 Private Disclosure Process

1. **DO NOT** open a public GitHub issue for security vulnerabilities
2. Email security details to: **fabrizio.salmi@gmail.com**
3. Include:
   - Description of the vulnerability
   - Steps to reproduce the issue
   - Potential impact assessment
   - Suggested fix (if available)

### ⏱️ Response Timeline

- **Initial Response**: Within 48 hours of report
- **Status Update**: Within 7 days with assessment and timeline
- **Resolution**: Depends on severity and complexity

### 🏆 Recognition

We appreciate security researchers and will:
- Acknowledge your contribution (if desired)
- Keep you informed of the fix progress
- Credit you in the security advisory (unless you prefer to remain anonymous)

## Security Best Practices

### For Users

When deploying Proxmox VM Autoscale:

1. **Credentials Management**:
   - Store `config.yaml` with restricted permissions: `chmod 600 /usr/local/bin/vm_autoscale/config.yaml`
   - Use SSH keys instead of passwords when possible
   - Rotate credentials regularly

2. **SSH Security**:
   - Use strong SSH keys (RSA 4096-bit or Ed25519)
   - Restrict SSH access to specific IP addresses
   - Keep SSH key files with permissions 600: `chmod 600 /path/to/ssh_key`

3. **System Security**:
   - Run the service with minimal required privileges
   - Keep Python and dependencies updated
   - Monitor logs for suspicious activity

4. **Network Security**:
   - Use firewall rules to restrict access to Proxmox hosts
   - Consider using a VPN or bastion host for SSH connections
   - Enable SSH rate limiting to prevent brute-force attacks

5. **Configuration Security**:
   - Never commit `config.yaml` with real credentials to version control
   - Use `.gitignore` to exclude sensitive configuration files
   - Backup configuration files securely

### For Developers

When contributing:

1. **Code Security**:
   - Validate and sanitize all user inputs
   - Use parameterized commands to prevent command injection
   - Handle exceptions properly to avoid information disclosure

2. **Dependency Security**:
   - Keep dependencies updated to latest secure versions
   - Review dependencies for known vulnerabilities
   - Use `pip-audit` or similar tools to check for CVEs

3. **Testing**:
   - Test with invalid/malicious inputs
   - Verify error messages don't leak sensitive information
   - Test authentication and authorization thoroughly

## Known Security Considerations

### Current Limitations

1. **SSH Credentials in Config**: 
   - Credentials are stored in plain text in `config.yaml`
   - **Mitigation**: Use file permissions (600) and SSH keys instead of passwords

2. **Logging Sensitive Data**:
   - Be careful not to log sensitive information
   - **Mitigation**: Review logs regularly and sanitize before sharing

3. **Privileged Access**:
   - Service requires root access to Proxmox hosts
   - **Mitigation**: Use dedicated service accounts with minimal required permissions where possible

### Future Enhancements

Planned security improvements:
- Support for encrypted credential storage
- Integration with secret management systems (Vault, etc.)
- Support for SSH agent authentication
- Audit logging for all scaling actions
- Role-based access control

## Security Update Process

When a security issue is identified:

1. A fix will be developed and tested privately
2. A security advisory will be published on GitHub
3. A new release will be tagged with the fix
4. Users will be notified through GitHub release notes

## Additional Resources

- [Proxmox VE Security](https://pve.proxmox.com/wiki/Security)
- [SSH Security Best Practices](https://www.ssh.com/academy/ssh/security)
- [Python Security Guidelines](https://python.readthedocs.io/en/latest/library/security_warnings.html)

---

**Note**: This is an alpha version project. Use in production environments should be done with appropriate testing and security measures in place.
