# Contributing to Partner

Thanks for your interest in contributing! Partner is an open-source project and we welcome contributions of all kinds.

## How to Contribute

### Report Bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your OS, Python version, and agent backend

### Suggest Features

Open an issue with:
- The problem you're trying to solve
- Your proposed solution
- Why it would be useful to others

### Submit Code

1. Fork the repo
2. Create a branch: `git checkout -b my-feature`
3. Make your changes
4. Run tests: `python tests/test_basic.py`
5. Commit: `git commit -m "feat: add my feature"`
6. Push: `git push origin my-feature`
7. Open a Pull Request

## Development Setup

```bash
git clone https://github.com/zty522/partner.git
cd partner
pip install -e .
python tests/test_basic.py
```

## Code Style

- Python 3.9+
- Use type hints where possible
- Keep functions focused and small
- Write docstrings for public APIs

## Adding a New Agent Backend

1. Create `partner/backends/my_agent.py`
2. Implement the `AgentAdapter` interface from `partner/adapter.py`
3. Register it in `partner/adapter.py:create_adapter()`
4. Add detection logic in `partner/setup.py`
5. Update the README's Supported Agents table

## License

By contributing, you agree that your contributions will be licensed under [Apache 2.0](LICENSE).
