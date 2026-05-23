# WeChat & QQ Integration Guide

Partner can connect to WeChat and QQ, allowing you to chat with your AI research companion through messaging apps.

## WeChat Integration

WeChat integration uses WeChatFerry (DLL injection) to hook into the WeChat desktop client. Since WeChatFerry only works on Windows, we use a WebSocket bridge to connect from WSL/Linux.

### Architecture

```
┌─────────────────┐     WebSocket      ┌─────────────────┐
│   WSL/Linux     │ ◄──────────────► │    Windows      │
│   Partner       │     (port 8765)    │   WeChatFerry   │
│   (this PC)     │                    │   Bridge        │
└─────────────────┘                    └────────┬────────┘
                                                │ DLL Hook
                                        ┌───────┴───────┐
                                        │  WeChat       │
                                        │  Desktop      │
                                        └───────────────┘
```

### Setup

#### 1. Windows Side (PowerShell)

```powershell
# Install WeChatFerry
pip install wcferry websockets

# Start the bridge
python -m partner.windows_bridge --port 8765
```

**Requirements:**
- Windows 10/11
- WeChat desktop client (specific version - check wcferry docs)
- WeChat must be logged in

#### 2. WSL/Linux Side

```bash
# Install websockets
pip install websockets

# Start the client
partner wechat --host <windows-ip> --port 8765
```

**Finding Windows IP from WSL:**
```bash
# Get Windows host IP
cat /etc/resolv.conf | grep nameserver | awk '{print $2}'
```

### Usage

Once connected, send messages to your WeChat account (or have others message you). Partner will respond automatically.

**Commands:**
```bash
# Basic usage
partner wechat

# Specify Windows host
partner wechat --host 192.168.1.100

# Disable voice
partner wechat --no-voice

# Enable voice replies
partner wechat --voice-reply
```

### Voice Messages

Voice messages are automatically transcribed using FunASR (default) or Whisper. To enable voice replies:

```bash
partner wechat --voice-reply
```

---

## QQ Integration

QQ integration uses NapCat, an NTQQ-based bot protocol that exposes OneBot 11 API.

### Architecture

```
┌─────────────────┐     WebSocket      ┌─────────────────┐
│   Partner       │ ◄──────────────► │    NapCat       │
│   (any OS)      │   (port 3001)      │   Protocol      │
└─────────────────┘                    └────────┬────────┘
                                                │ NTQQ API
                                        ┌───────┴───────┐
                                        │  QQ Client    │
                                        └───────────────┘
```

### Setup

#### 1. Install NapCat

Follow the NapCat installation guide: https://github.com/NapNeko/NapCatQQ

#### 2. Configure NapCat

Configure NapCat to enable WebSocket forward:
- WebSocket forward port: 3001 (default)
- Enable message receiving

#### 3. Start Partner QQ Bridge

```bash
# Install websockets
pip install websockets

# Start the bridge
partner qq --url ws://127.0.0.1:3001
```

### Usage

Once connected, send messages to your QQ account. Partner will respond automatically.

**Commands:**
```bash
# Basic usage (default: ws://127.0.0.1:3001)
partner qq

# Specify NapCat URL
partner qq --url ws://192.168.1.100:3001

# Disable voice
partner qq --no-voice

# Enable voice replies
partner qq --voice-reply
```

### Group Chat

By default, Partner only responds to private messages. To respond in groups, configure NapCat to send group messages and update the bridge config.

---

## Comparison

| Feature | WeChat | QQ |
|---------|--------|-----|
| Platform | Windows only (bridge for WSL) | Any OS |
| Protocol | WeChatFerry (DLL) | NapCat (OneBot 11) |
| Voice | ✅ Supported | ✅ Supported |
| Group Chat | ⚠️ Limited | ✅ Supported |
| Setup Complexity | Medium (bridge needed) | Easy |

---

## Troubleshooting

### WeChat

**"WeChatFerry not installed"**
```bash
pip install wcferry
```

**"WeChat not logged in"**
- Open WeChat desktop client
- Login with your account
- Keep WeChat running

**"Connection refused" from WSL**
- Check Windows firewall
- Ensure bridge is running: `python -m partner.windows_bridge`
- Try different port: `--port 8766`

### QQ

**"websockets not installed"**
```bash
pip install websockets
```

**"Connection refused"**
- Check NapCat is running
- Verify WebSocket URL
- Check NapCat logs for errors

---

## API Reference

### Windows Bridge

```bash
python -m partner.windows_bridge [options]

Options:
  --port PORT    WebSocket port (default: 8765)
```

### WSL Client

```bash
partner wechat [options]

Options:
  -w, --workspace PATH   Partner workspace
  --host HOST           Windows bridge host
  --port PORT           Windows bridge port (default: 8765)
  --no-voice            Disable voice
  --voice-reply         Reply with voice
```

### QQ Bridge

```bash
partner qq [options]

Options:
  -w, --workspace PATH   Partner workspace
  --url URL             NapCat WebSocket URL
  --no-voice            Disable voice
  --voice-reply         Reply with voice
```
