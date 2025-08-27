# Cross-Platform Clipboard Sync

A simple Python application that synchronizes clipboard contents (text and images) between devices on the same network.

## Features

- **Real-time sync**: Automatically detects clipboard changes and syncs them
- **Cross-platform**: Works on Windows, macOS, and Linux
- **Text support**: Syncs text clipboard content
- **Size limits**: Configurable maximum clipboard size (default: 10MB)
- **Network-based**: Uses HTTP for communication between devices
- **Deduplication**: Prevents infinite loops and duplicate updates

## Usage

### Basic Setup (Two Devices)

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

### Command Line Options

- `--port`: Port for the server (default: 8765)
- `--peers`: IP addresses of other devices (space-separated)
- `--max-size`: Maximum clipboard size in MB (default: 10)
- `--server-only`: Run as server only (no clipboard monitoring)

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

## How It Works

1. **Dual Mode**: Each device runs both a server (to receive updates) and a client (to send updates)
2. **Monitoring**: Continuously monitors the local clipboard for changes
3. **Detection**: Uses content hashing to detect when clipboard content changes
4. **Transmission**: Sends updates to all peer devices via HTTP POST
5. **Reception**: Receives updates from peers and updates the local clipboard
6. **Deduplication**: Prevents loops by tracking content hashes

## Network Requirements

- All devices must be on the same network
- Port 8765 (or custom port) must be accessible between devices
- Firewall may need to allow incoming connections on the specified port

## Limitations

- Currently optimized for text content
- Image support is basic (framework is there for future enhancement)
- No persistence (clipboard is only synced while running)
- No encryption (data is sent over HTTP)

## Troubleshooting

1. **Connection issues**: Check firewall settings and ensure devices are on same network
2. **Permission errors**: On some systems, clipboard access may require special permissions
3. **Port conflicts**: Try a different port with `--port` option

## Security Note

This application sends clipboard data over HTTP without encryption. Only use on trusted networks.
