# Contributing to beacon-skill

Thank you for your interest in contributing to beacon-skill! This guide will help you get started.

## 🎯 How to Contribute

### 1. Find an Issue
- Check open issues on GitHub
- Look for issues labeled `good first issue` or `help wanted`
- Issues with RTC bounties are marked with bounty amounts

### 2. Fork and Clone
```bash
git clone https://github.com/YOUR_USERNAME/beacon-skill.git
cd beacon-skill
```

### 3. Create a Branch
```bash
git checkout -b feature/your-feature-name
```

### 4. Make Changes
- Follow existing code style
- Add tests for new features
- Update documentation as needed

### 5. Test Your Changes
```bash
npm test
# or
pytest
```

### 6. Submit a PR
- Push your branch to your fork
- Create a pull request with a clear description
- Link to the related issue

## 📋 Development Setup

### Prerequisites
- Node.js 18+ or Python 3.9+
- Git

### Install Dependencies
```bash
# For Node.js
npm install

# For Python
pip install -e .
```

### Configure Beacon
```bash
cp config.example.json config.json
# Edit config.json with your settings
```

## 🏷️ PR Guidelines

### Commit Messages
Use conventional commits:
- `feat: add new feature`
- `fix: resolve bug`
- `docs: update documentation`
- `test: add tests`
- `refactor: improve code structure`

### PR Title Format
```
<type>: <description> (fixes #issue-number)
```

Example: `feat: add JSON output mode (fixes #73)`

## 💰 RTC Bounties

Contributions can earn RTC tokens:
- Bug fixes: 2-5 RTC
- Features: 5-15 RTC
- Documentation: 2-8 RTC
- Community files: 1-3 RTC

## 🤝 Questions?

- Open an issue for questions
- Join the community discussions
- Check existing documentation

## 📜 Code of Conduct

Be respectful and inclusive. We welcome contributions from everyone.

---

Thanks for contributing! 🚀
