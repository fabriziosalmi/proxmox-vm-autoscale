# Contributing to Proxmox VM Autoscale

Thank you for your interest in contributing to Proxmox VM Autoscale! We appreciate pull requests, bug reports, and feature suggestions.

## 🚀 Quick Start for Contributors

### Prerequisites
- Python 3.6 or higher
- Git installed and configured
- Access to a Proxmox VE environment for testing (optional but recommended)

### Setting Up Your Development Environment

1. **Fork the repository** on GitHub

2. **Clone your fork** to your local machine:
   ```bash
   git clone https://github.com/YOUR_USERNAME/proxmox-vm-autoscale.git
   cd proxmox-vm-autoscale
   ```

3. **Install dependencies**:
   ```bash
   pip3 install -r requirements.txt
   ```

4. **Configure the test environment**:
   - Copy `config.yaml` and update with your test environment details
   - Ensure SSH access to your test Proxmox host(s)

## 📝 How to Contribute

### Reporting Bugs
- Use the [GitHub Issues](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/new/choose) page
- Include:
  - Detailed description of the bug
  - Steps to reproduce
  - Expected vs. actual behavior
  - Your environment (Python version, Proxmox version, OS)
  - Relevant log excerpts (sanitize any sensitive information)

### Suggesting Enhancements
- Open an issue with the "Feature Request" template
- Clearly describe the enhancement and its benefits
- Include use cases and examples if possible

### Submitting Pull Requests

1. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   # or for bug fixes:
   git checkout -b fix/issue-description
   ```

2. **Make your changes**:
   - Follow the existing code style
   - Add comments for complex logic
   - Update documentation if needed

3. **Test your changes**:
   ```bash
   python3 autoscale.py
   # Verify functionality with your test VMs
   ```

4. **Commit your changes** with clear, descriptive messages:
   ```bash
   git add .
   git commit -m "feat: add support for custom scaling intervals per VM"
   # or
   git commit -m "fix: resolve SSH connection timeout issue"
   ```

5. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Open a Pull Request** on GitHub:
   - Provide a clear title and description
   - Reference any related issues
   - Explain what changes were made and why

## 🧪 Testing Guidelines

### Manual Testing
- Test with different VM configurations
- Verify scaling up and down for both CPU and RAM
- Test with various threshold configurations
- Ensure notifications work correctly (if configured)

### Testing Checklist
Before submitting a PR, ensure:
- [ ] Code runs without errors
- [ ] SSH connections work properly
- [ ] Scaling actions are logged correctly
- [ ] Configuration changes are documented
- [ ] No sensitive information is committed

## 📏 Coding Standards

### Python Style
- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) guidelines
- Use meaningful variable and function names
- Add docstrings to functions and classes
- Keep functions focused and concise

### Code Structure
- Maintain the existing project structure
- Use proper error handling with try/except blocks
- Log important events and errors appropriately
- Use type hints where applicable

### Documentation
- Update README.md if adding new features
- Update config.yaml.example for new configuration options
- Add inline comments for complex logic
- Update CHANGELOG if the project has one

## 🔒 Security Considerations

- Never commit credentials or sensitive data
- Sanitize logs and examples of any private information
- Report security vulnerabilities privately to the maintainers
- Follow secure coding practices

## 📋 Branch Naming Convention

- `feature/description` - For new features
- `fix/description` - For bug fixes
- `docs/description` - For documentation updates
- `refactor/description` - For code refactoring
- `test/description` - For adding or updating tests

## 🎯 Need Help?

If you need assistance:
- Check existing issues and discussions
- Ask questions in your PR or issue
- Be patient and respectful

## 📜 License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to Proxmox VM Autoscale! Your efforts help make this project better for everyone. 🎉
