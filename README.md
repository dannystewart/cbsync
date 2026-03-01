# cbsync: Cross-Platform Clipboard Sync

A simple Python application that synchronizes clipboard **text and images** between devices on the same network.

## Features

- **Real-time sync**: Automatically detects clipboard changes and syncs them
- **Cross-platform**: Works on Windows, macOS, and Linux
- **Network discovery**: Automatically find other clipboard sync instances on your network
- **Size limits**: Configurable maximum clipboard size (default: 10MB)
- **Image sync (macOS + Windows)**: Copies screenshots and small images (sent as PNG)
- **Network-based**: Uses HTTP for communication between devices
- **Deduplication**: Prevents infinite loops and duplicate updates

The application will automatically scan your local network and find other clipboard sync instances. Port 8765 (or a custom port) must be accessible between devices. Network discovery will scan the local subnet (e.g., 192.168.1.*). If you have multiple network interfaces (e.g., Wi-Fi and Ethernet), you can specify which one to use with the `--interface` option.

When starting, the application displays its IP address and the exact command other devices can use to connect to it.

### Command Line Options

- `--port`: Port for the server (default: 8765)
- `--peers`: IP addresses of other devices (space-separated)
- `--max-size`: Maximum clipboard size in MB (default: 10)
- `--interface`: Specify network interface IP (e.g., 192.168.1.100) for discovery

### Examples

**Auto-discovery:**

```bash
cbsync
```

**Multiple peers:**

```bash
cbsync --peers 192.168.1.100 192.168.1.101 192.168.1.102
```

**Custom port:**

```bash
cbsync --port 9000 --peers 192.168.1.100
```

**Auto-discovery with specific interface:**

```bash
cbsync --interface 192.168.1.100
```

## Process Management

For easier process management, you can use the included management script:

```bash
# Start the application
cbsman start

# Check status
cbsman status

# View logs
cbsman logs

# Stop the application
cbsman stop

# Restart the application
cbsman restart
```

The management script handles graceful shutdown and prevents orphaned processes.
