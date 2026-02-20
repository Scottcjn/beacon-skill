# Beacon Skill Usage Examples

This document provides quick examples for using the Beacon skill.

## Basic Usage

### Installation

```bash
# Install via npm
npm install beacon-skill

# Or via ClawHub
clawhub install beacon-skill
```

### Quick Start

```bash
# Initialize beacon
beacon init

# Check status
beacon status

# Start listening
beacon listen --port 8080
```

### Configuration

```bash
# Set API endpoint
beacon config set endpoint https://api.example.com

# View current config
beacon config show
```

### Running Tests

```bash
# Run all tests
beacon test

# Run specific test
beacon test --filter "health"
```

---

*Added 2026-02-20 for improved documentation. Part of Bounty #255.*
