# Architecture Overview

## 📐 System Architecture

Proxmox VM Autoscale is designed as a monitoring and control service that runs continuously, checking VM resource usage and making scaling decisions based on configured thresholds.

```
┌─────────────────────────────────────────────────────────────┐
│                    VM Autoscale Service                     │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │   Config     │  │   Logging    │  │  Notification   │  │
│  │   Manager    │  │   System     │  │    Manager      │  │
│  └──────────────┘  └──────────────┘  └─────────────────┘  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           VMAutoscaler (Main Controller)             │  │
│  │  - Monitors VMs based on check_interval              │  │
│  │  - Coordinates scaling decisions                     │  │
│  │  - Handles errors and notifications                  │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                  │
│         ┌────────────────┴────────────────┐                │
│         │                                  │                │
│  ┌──────▼──────────┐              ┌───────▼─────────────┐  │
│  │ Host Resource   │              │   VM Resource       │  │
│  │    Checker      │              │     Manager         │  │
│  │                 │              │                     │  │
│  │ - Check host    │              │ - Get VM stats      │  │
│  │   CPU/RAM       │              │ - Scale CPU/RAM     │  │
│  │ - Prevent       │              │ - Manage cooldown   │  │
│  │   overload      │              │                     │  │
│  └─────────────────┘              └─────────────────────┘  │
│         │                                  │                │
└─────────┼──────────────────────────────────┼────────────────┘
          │                                  │
          │          SSH Connection          │
          │      ┌──────────────────┐        │
          └──────►   SSH Utils      ◄────────┘
                 │   - Connect      │
                 │   - Execute      │
                 │   - Retry logic  │
                 └────────┬─────────┘
                          │
          ┌───────────────▼──────────────────┐
          │       Proxmox VE Host(s)         │
          │                                  │
          │   ┌─────┐  ┌─────┐  ┌─────┐    │
          │   │VM101│  │VM102│  │VM103│    │
          │   └─────┘  └─────┘  └─────┘    │
          └──────────────────────────────────┘
```

## 🏗️ Component Breakdown

### Core Components

#### 1. **autoscale.py** - Main Application
- **VMAutoscaler**: Main orchestrator class
  - Loads configuration and initializes logging
  - Manages the continuous monitoring loop
  - Coordinates between different managers
  - Handles top-level error handling

- **NotificationManager**: Notification handler
  - Supports Gotify push notifications
  - Supports SMTP email alerts
  - Formats and validates notification content
  - Manages multiple notification channels

#### 2. **vm_manager.py** - VM Resource Management
- **VMResourceManager**: Per-VM resource controller
  - Retrieves current CPU and RAM usage from VMs
  - Makes scaling decisions based on thresholds
  - Executes scaling commands (qm set)
  - Implements cooldown periods to prevent rapid scaling
  - Thread-safe scaling with locks

**Key Methods:**
- `get_resource_usage()`: Parses pvesh output for CPU/RAM metrics
- `scale_cpu(direction)`: Scales CPU cores and vCPUs up or down
- `scale_ram(direction)`: Adjusts RAM allocation
- `can_scale()`: Enforces cooldown period between operations

#### 3. **ssh_utils.py** - SSH Connection Manager
- **SSHClient**: Manages SSH connections to Proxmox hosts
  - Supports password and key-based authentication
  - Implements connection retry logic with exponential backoff
  - Maintains persistent connections when possible
  - Provides command execution with error handling

**Features:**
- Automatic reconnection on connection loss
- Context manager support for resource cleanup
- Configurable timeouts and retry attempts

#### 4. **host_resource_checker.py** - Host Monitoring
- **HostResourceChecker**: Monitors Proxmox host resources
  - Prevents scaling when host resources are constrained
  - Checks CPU and RAM usage thresholds
  - Parses pvesh JSON output for host statistics

**Safety Feature:**
- Blocks VM scaling if host is above configured limits
- Prevents resource exhaustion on Proxmox hosts

## 🔄 Operation Flow

### Monitoring Loop

```
1. Load Configuration
   └─→ config.yaml, logging_config.json

2. Initialize Components
   ├─→ SSH clients for each Proxmox host
   ├─→ Notification manager
   └─→ Logger

3. Main Loop (every check_interval seconds):
   │
   ├─→ For each Proxmox host:
   │   │
   │   ├─→ For each VM on that host:
   │   │   │
   │   │   ├─→ Check if VM is running
   │   │   │
   │   │   ├─→ Check host resource availability
   │   │   │   (CPU/RAM within limits?)
   │   │   │
   │   │   ├─→ Get VM resource usage
   │   │   │   (via pvesh get /cluster/resources)
   │   │   │
   │   │   ├─→ Evaluate CPU scaling:
   │   │   │   ├─→ Usage > high threshold? → Scale up
   │   │   │   └─→ Usage < low threshold? → Scale down
   │   │   │
   │   │   ├─→ Evaluate RAM scaling:
   │   │   │   ├─→ Usage > high threshold? → Scale up
   │   │   │   └─→ Usage < low threshold? → Scale down
   │   │   │
   │   │   ├─→ Execute scaling if needed
   │   │   │   (respecting cooldown period)
   │   │   │
   │   │   └─→ Send notification on scaling action
   │   │
   │   └─→ Close SSH connection
   │
   └─→ Sleep for check_interval
```

### Scaling Decision Process

```
┌─────────────────────────────────────────┐
│  VM Resource Check                      │
└────────────┬────────────────────────────┘
             │
             ├─→ Is VM running?
             │   └─→ No: Skip
             │
             ├─→ Are host resources available?
             │   └─→ No: Skip (log warning)
             │
             ├─→ Get current usage metrics
             │
             ├─→ Check cooldown period
             │   └─→ Not elapsed: Skip
             │
             ├─→ Evaluate thresholds:
             │   ├─→ CPU > high → Scale CPU up
             │   ├─→ CPU < low → Scale CPU down
             │   ├─→ RAM > high → Scale RAM up
             │   └─→ RAM < low → Scale RAM down
             │
             ├─→ Execute qm set command
             │
             └─→ Send notification
```

## 📁 File Structure

```
/usr/local/bin/vm_autoscale/
├── autoscale.py              # Main application entry point
├── vm_manager.py             # VM resource management logic
├── ssh_utils.py              # SSH connection handling
├── host_resource_checker.py  # Host resource monitoring
├── config.yaml               # Main configuration file
├── logging_config.json       # Logging configuration
├── requirements.txt          # Python dependencies
└── vm_autoscale.service      # Systemd service definition
```

## 🔐 Security Model

1. **Authentication**: 
   - SSH key-based (preferred) or password authentication
   - Credentials stored in config.yaml (file permissions: 600)

2. **Authorization**: 
   - Requires root access to Proxmox hosts for qm commands
   - Service runs as root user (configurable in systemd)

3. **Network Security**: 
   - Direct SSH connections to Proxmox hosts
   - No external API dependencies (except optional Gotify)

## 📊 Data Flow

### Configuration Data
```
config.yaml → VMAutoscaler → Components
```

### Runtime Data
```
Proxmox Host → SSH → VMResourceManager → Decision Logic → Proxmox Host
                                              ↓
                                     NotificationManager
                                              ↓
                                    Gotify/Email/Logs
```

## 🔧 Extension Points

### Adding New Features

1. **New Notification Channels**: 
   - Add methods to `NotificationManager` class
   - Update configuration schema in config.yaml

2. **Custom Scaling Algorithms**: 
   - Extend `VMResourceManager` with new decision logic
   - Add configuration options for algorithm parameters

3. **Additional Metrics**: 
   - Modify `get_resource_usage()` to parse additional pvesh fields
   - Update threshold logic as needed

4. **Multi-tier Scaling**: 
   - Implement graduated scaling amounts based on severity
   - Add configuration for scaling tiers

## 🧪 Testing Strategy

### Manual Testing
- Test with actual Proxmox VMs in a lab environment
- Verify scaling behavior under different load conditions
- Test notification delivery

### Unit Testing (Future)
- Mock SSH connections for unit tests
- Test scaling logic with various threshold combinations
- Validate configuration parsing

### Integration Testing (Future)
- End-to-end tests with test VMs
- Verify cooldown periods
- Test error recovery mechanisms

## 🚀 Deployment Architecture

### Typical Deployment

```
┌─────────────────────────────────────────────┐
│  Control Machine (Linux)                    │
│  ┌───────────────────────────────────────┐  │
│  │  VM Autoscale Service                 │  │
│  │  - Runs as systemd service            │  │
│  │  - Managed by systemctl               │  │
│  │  - Logs to /var/log/vm_autoscale.log │  │
│  └───────────────────────────────────────┘  │
└────────────┬────────────────────────────────┘
             │ SSH (Port 22)
             │
     ┌───────┴────────┬────────────┐
     │                │            │
┌────▼────┐     ┌────▼────┐  ┌───▼─────┐
│Proxmox  │     │Proxmox  │  │Proxmox  │
│ Host 1  │     │ Host 2  │  │ Host 3  │
│         │     │         │  │         │
│ VMs...  │     │ VMs...  │  │ VMs...  │
└─────────┘     └─────────┘  └─────────┘
```

### Alternative: On-Host Deployment
The service can also run directly on a Proxmox host to manage local VMs, though multi-host management requires external deployment.

## 🔮 Future Architecture Considerations

1. **Database Backend**: Store historical metrics for trend analysis
2. **Web UI**: Real-time dashboard for monitoring and configuration
3. **Multi-node Clustering**: HA deployment with failover
4. **API Interface**: RESTful API for external integrations
5. **Webhook Support**: Trigger scaling from external systems
6. **Machine Learning**: Predictive scaling based on historical patterns

---

For more detailed information about specific components, refer to the inline documentation in each Python module.
