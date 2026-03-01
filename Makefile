.PHONY: install dev test lint format clean help run run-debug run-local bot-stop bot-status bot-logs bot-attach

# Default target
help:
	@echo "Available commands:"
	@echo "  install    - Install production dependencies"
	@echo "  dev        - Install development dependencies"
	@echo "  test       - Run tests"
	@echo "  lint       - Run linting checks"
	@echo "  format     - Format code"
	@echo "  clean      - Clean up generated files"
	@echo "  run        - Detached restart in tmux session (recommended default)"
	@echo "  run-debug  - Detached restart in tmux session with --debug"
	@echo "  run-local  - Run bot in current foreground shell"
	@echo "  bot-stop   - Stop tmux bot session and residual bot processes"
	@echo "  bot-status - Show tmux/process status (expects exactly one bot process)"
	@echo "  bot-logs   - Tail recent tmux bot logs"
	@echo "  bot-attach - Attach to tmux bot session"

install:
	poetry install --no-dev

dev:
	poetry install
	poetry run pre-commit install --install-hooks || echo "pre-commit not configured yet"

test:
	poetry run pytest

lint:
	poetry run black --check src tests
	poetry run isort --check-only src tests
	poetry run flake8 src tests
	poetry run mypy src

format:
	poetry run black src tests
	poetry run isort src tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov/ .pytest_cache/ dist/ build/

run:
	./scripts/tmux-bot.sh restart-detached

# For debugging
run-debug:
	BOT_DEBUG=1 ./scripts/tmux-bot.sh restart-detached

run-local:
	poetry run cli-tg-bot

bot-stop:
	./scripts/tmux-bot.sh stop

bot-status:
	./scripts/tmux-bot.sh status

bot-logs:
	./scripts/tmux-bot.sh logs

bot-attach:
	./scripts/tmux-bot.sh attach
