# Beacon Skill

[![Bounty](https://img.shields.io/badge/bounty-RTC-green)](https://github.com/Scottcjn/rustchain-bounties)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Contributors](https://img.shields.io/github/contributors/Scottcjn/beacon-skill)](https://github.com/Scottcjn/beacon-skill/graphs/contributors)

> **Beacon** is an AI agent heartbeat and coordination protocol. It enables agents to maintain presence, share status, and coordinate activities through periodic heartbeat messages with optional RTC token validation.

## 🚀 Features

- **Heartbeat Protocol**: Lightweight agent-to-agent pings
- **UDP Broadcast**: Efficient local network discovery
- **RTC Integration**: Optional crypto token validation
- **Multi-Agent Support**: Coordinate swarms and teams
- **JSON Output**: Machine-readable status and metrics
- **Cross-Platform**: Works on macOS, Linux, and Windows

## 📦 Installation

### Homebrew (macOS)

```bash
brew tap Scottcjn/homebrew-tap
brew install beacon
```

### Linux

```bash
wget https://github.com/Scottcjn/beacon-skill/releases/latest/download/beacon-linux-amd64
chmod +x beacon-linux-amd64
sudo mv beacon-linux-amd64 /usr/local/bin/beacon
```

### From Source

```bash
git clone https://github.com/Scottcjn/beacon-skill.git
cd beacon-skill
cargo build --release
```

## 💡 Usage

### Start Agent

```bash
# Basic start
beacon start

# With wallet
beacon start --wallet your-wallet-address

# Listener mode
beacon start --listen-only
```

### Send Heartbeat

```bash
# Simple ping
beacon ping

# With message
beacon ping --message "Working on documentation"

# JSON output
beacon ping --json
```

### Check Status

```bash
# Local status
beacon status

# JSON format
beacon status --json

# Specific agent
beacon status --agent agent-id
```

## 📖 Documentation

- [Getting Started Tutorial](TUTORIALS/getting-started.md)
- [API Reference](docs/API.md)
- [Configuration Guide](docs/CONFIGURATION.md)
- [Integration Examples](docs/INTEGRATION.md)

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

### Quick Start

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run tests: `cargo test`
5. Submit a PR

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🏆 Bounties

This project participates in the RustChain bounty program:

- **Documentation**: 2-5 RTC per improvement
- **Features**: 5-20 RTC per feature
- **Bug Fixes**: 3-10 RTC per fix

Claim bounties with wallet: `joshualover-dev`

See [rustchain-bounties](https://github.com/Scottcjn/rustchain-bounties) for available opportunities.

## 🔗 Links

- [RustChain](https://github.com/Scottcjn/Rustchain)
- [Bounty Program](https://github.com/Scottcjn/rustchain-bounties)
- [Discord Community](https://discord.gg/rustchain)

---

**Built with ❤️ by the RustChain Team**
