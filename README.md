# Cross-Platform Clipboard Sync

A simple Python application that synchronizes clipboard text content between devices on the same network.

## Features

- **Real-time sync**: Automatically detects clipboard changes and syncs them
- **Cross-platform**: Works on Windows, macOS, and Linux
- **Network discovery**: Automatically find other clipboard sync instances on your network
- **Size limits**: Configurable maximum clipboard size (default: 10MB)
- **Network-based**: Uses HTTP for communication between devices
- **Deduplication**: Prevents infinite loops and duplicate updates

## Usage

### Basic Setup for Two Devices

#### Automatic Network Discovery

1. **Start on Device 1**:

   ```bash
   python clipboard_sync.py
   ```

2. **Start on Device 2**:

   ```bash
   python clipboard_sync.py
   ```

#### Manual IP Configuration

1. **Find IP addresses**: On each device, find the local IP address
   - Windows: `ipconfig`
   - Mac/Linux: `ifconfig` or `ip addr`

2. **Start on Device 1** (e.g., Windows PC at 192.168.1.100):

   ```bash
   python clipboard_sync.py --peers 192.168.1.101
   ```

3. **Start on Device 2** (e.g., Mac at 192.168.1.101):

   ```bash
   python clipboard_sync.py --peers 192.168.1.100
   ```

The application will automatically scan your local network and find other clipboard sync instances. Port 8765 (or a custom port) must be accessible between devices. Network discovery will scan the local subnet (e.g., 192.168.1.*).

The application automatically detects the best network interface to use. If you have multiple network interfaces (e.g., Wi-Fi and Ethernet), you can specify which one to use with the `--interface` option.

When starting, the application displays its IP address and the exact command other devices can use to connect to it.

### Command Line Options

- `--port`: Port for the server (default: 8765)
- `--peers`: IP addresses of other devices (space-separated)
- `--max-size`: Maximum clipboard size in MB (default: 10)
- `--server-only`: Run as server only (no clipboard monitoring)
- `--interface`: Specify network interface IP (e.g., 192.168.1.100) for discovery

### Examples

**Multiple peers:**

```bash
python clipboard_sync.py --peers 192.168.1.100 192.168.1.101 192.168.1.102
```

**Custom port:**

```bash
python clipboard_sync.py --port 9000 --peers 192.168.1.100
```

**Server only mode:**

```bash
python clipboard_sync.py --server-only
```

**Auto-discovery:**

```bash
python clipboard_sync.py
```

**Auto-discovery with specific interface:**

```bash
python clipboard_sync.py --interface 192.168.1.100
```

## How It Works

1. **Dual Mode**: Each device runs both a server (to receive updates) and a client (to send updates)
2. **Monitoring**: Continuously monitors the local clipboard for changes
3. **Detection**: Uses content hashing to detect when clipboard content changes
4. **Transmission**: Sends updates to all peer devices via HTTP POST
5. **Reception**: Receives updates from peers and updates the local clipboard
6. **Deduplication**: Prevents loops by tracking content hashes
7. **Discovery**: Scans local network to find other clipboard sync instances

## Installation

### Using Poetry (Recommended)

```bash
# Clone the repository
git clone <repository-url>
cd clipboard-sync

# Install dependencies
poetry install

# Run the application
poetry run python src/clipboard_sync/clipboard_sync.py
```

### Using pip

```bash
# Install dependencies
pip install flask polykit pyperclip requests netifaces

# Run the application
python src/clipboard_sync/clipboard_sync.py
```

## Notes

- **This sends clipboard data over HTTP without encryption and should only be used on trusted networks**
- The application runs in the background and monitors clipboard changes
- Press Ctrl+C in the terminal to stop the application
- Text content is synced in real-time across all platforms

## Troubleshooting

1. **Connection issues**: Check firewall settings and ensure devices are on same network
2. **Permission errors**: On some systems, clipboard access may require special permissions
3. **Port conflicts**: Try a different port with `--port` option
4. **Discovery not working**: Ensure all devices are on the same subnet and firewalls allow the port
5. **Wrong network interface**: Use `--interface` to specify the correct network interface IP
