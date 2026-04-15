.PHONY: help install install-dev test lint clean uninstall check

# Default target
help:
	@echo "Ave Guardian — Makefile"
	@echo ""
	@echo "Usage:"
	@echo "  make install       Install ave-guardian (requires OpenClaw)"
	@echo "  make install-dev  Install with development dependencies"
	@echo "  make test         Run tests"
	@echo "  make lint         Run linter (ruff)"
	@echo "  make format       Format code (black)"
	@echo "  make clean        Remove build artifacts"
	@echo "  make uninstall    Uninstall ave-guardian"
	@echo "  make check        Run all checks"

# Install the package
install:
	@echo "Installing ave-guardian..."
	@command -v openclaw >/dev/null 2>&1 || { echo "OpenClaw not found. Install from https://docs.openclaw.ai"; exit 1; }
	@pip install -e .
	@echo "Done. Restart your OpenClaw agent to load the skill."

# Install with dev dependencies
install-dev:
	@pip install -e ".[dev]"
	@echo "Dev dependencies installed."

# Run tests
test:
	@pytest tests/ -v

# Lint with ruff
lint:
	@ruff check scripts/

# Format with black
format:
	@black scripts/ tests/

# Clean build artifacts
clean:
	@rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "Clean done."

# Uninstall
uninstall:
	@pip uninstall ave-guardian -y || true
	@echo "Uninstalled."

# Run all checks
check: lint test

# Copy cron files to ~/.openclaw/ (called by install)
install-crons:
	@mkdir -p ~/.openclaw/cron/
	@cp cron/*_cron.sh ~/.openclaw/cron/ave-guardian- 2>/dev/null || true
	@echo "Cron jobs installed to ~/.openclaw/cron/"
