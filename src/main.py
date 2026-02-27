"""Main entry point for CLITG."""

import argparse
import asyncio
import logging
import os
import re
import shutil
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

import structlog

from src import __version__
from src.bot.core import ClaudeCodeBot
from src.claude import (
    ClaudeIntegration,
    ClaudeProcessManager,
    SessionManager,
    ToolMonitor,
)
from src.claude.permissions import PermissionManager
from src.claude.sdk_integration import ClaudeSDKManager
from src.config.settings import Settings
from src.exceptions import ConfigurationError
from src.security.audit import AuditLogger, SQLiteAuditStorage
from src.security.auth import (
    AuthenticationManager,
    InMemoryTokenStorage,
    TokenAuthProvider,
    WhitelistAuthProvider,
)
from src.security.rate_limiter import RateLimiter
from src.security.validators import SecurityValidator
from src.services import (
    ApprovalService,
    EventService,
    SessionInteractionService,
    SessionLifecycleService,
    SessionService,
)
from src.storage.facade import Storage
from src.storage.session_storage import SQLiteSessionStorage

from src.bot.utils.cc_switch import CCSwitchManager

_TELEGRAM_BOT_TOKEN_IN_URL_RE = re.compile(
    r"(https?://api\.telegram\.org/bot)([^/\s]+)"
)
_TELEGRAM_BOT_TOKEN_RAW_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_DEFAULT_LOG_FILE = Path("logs/bot.log")
_LOG_FILE_MAX_BYTES = 10 * 1024 * 1024
_LOG_FILE_BACKUP_COUNT = 5


def redact_sensitive_text(text: str) -> str:
    """Redact sensitive tokens from log text."""
    redacted = _TELEGRAM_BOT_TOKEN_IN_URL_RE.sub(r"\1<redacted>", text)
    redacted = _TELEGRAM_BOT_TOKEN_RAW_RE.sub("<redacted_token>", redacted)
    return redacted


class SensitiveLogFilter(logging.Filter):
    """Filter log records to avoid leaking secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_sensitive_text(message)
        if redacted != message:
            # Keep a pre-formatted safe message to avoid re-inserting args.
            record.msg = redacted
            record.args = ()
        return True


def _resolve_log_file_path() -> Optional[Path]:
    """Resolve target file path for rotating logs."""
    raw_path = os.getenv("CLITG_LOG_FILE", str(_DEFAULT_LOG_FILE)).strip()
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def _configure_rotating_file_logging(
    level: int, sensitive_filter: logging.Filter
) -> None:
    """Attach rotating file handler for persistent diagnostics."""
    log_path = _resolve_log_file_path()
    if log_path is None:
        return

    root_logger = logging.getLogger()

    try:
        resolved_target = log_path.resolve(strict=False)
        for handler in root_logger.handlers:
            base_filename = getattr(handler, "baseFilename", None)
            if not isinstance(base_filename, str):
                continue
            if Path(base_filename).resolve(strict=False) == resolved_target:
                return

        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=log_path,
            maxBytes=_LOG_FILE_MAX_BYTES,
            backupCount=_LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        file_handler.addFilter(sensitive_filter)
        root_logger.addHandler(file_handler)
        root_logger.info("File logging enabled: %s", str(log_path))
    except Exception as exc:
        root_logger.warning(
            "Failed to configure rotating file logging: path=%s error=%s type=%s",
            str(log_path),
            str(exc),
            type(exc).__name__,
        )


def setup_logging(debug: bool = False) -> None:
    """Configure structured logging."""
    level = logging.DEBUG if debug else logging.INFO

    # Configure standard logging
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stdout,
    )
    # Always apply secret redaction filter to root handlers.
    sensitive_filter = SensitiveLogFilter()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(sensitive_filter)
    _configure_rotating_file_logging(level, sensitive_filter)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            (
                structlog.processors.JSONRenderer()
                if not debug
                else structlog.dev.ConsoleRenderer()
            ),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="CLITG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--version", action="version", version=f"CLITG {__version__}")

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    parser.add_argument("--config-file", type=Path, help="Path to configuration file")

    return parser.parse_args()


async def create_application(config: Settings) -> Dict[str, Any]:
    """Create and configure the application components."""
    logger = structlog.get_logger()
    logger.info("Creating application components")

    # Initialize storage system
    storage = Storage(config.database_url)
    await storage.initialize()

    # Create security components
    providers = []

    # Add whitelist provider if users are configured
    if config.allowed_users:
        providers.append(WhitelistAuthProvider(config.allowed_users))

    # Add token provider if enabled
    if config.enable_token_auth:
        token_storage = InMemoryTokenStorage()  # TODO: Use database storage
        providers.append(TokenAuthProvider(config.auth_token_secret, token_storage))

    # Fall back to allowing all users in development mode
    if not providers and config.development_mode:
        logger.warning(
            "No auth providers configured - "
            "creating development-only allow-all provider"
        )
        providers.append(WhitelistAuthProvider([], allow_all_dev=True))
    elif not providers:
        raise ConfigurationError("No authentication providers configured")

    auth_manager = AuthenticationManager(providers)
    security_validator = SecurityValidator(config.approved_directory)
    rate_limiter = RateLimiter(config)

    # Create audit storage and logger
    audit_storage = SQLiteAuditStorage(storage.audit)
    audit_logger = AuditLogger(audit_storage)

    # Create Claude integration components with persistent storage
    session_storage = SQLiteSessionStorage(storage.db_manager)
    session_manager = SessionManager(config, session_storage)
    tool_monitor = ToolMonitor(config, security_validator)
    permission_manager = PermissionManager(approval_repository=storage.approvals)
    await permission_manager.initialize()
    approval_service = ApprovalService()
    session_lifecycle_service = SessionLifecycleService(
        permission_manager=permission_manager
    )
    session_interaction_service = SessionInteractionService()
    event_service = EventService(storage)
    session_service = SessionService(storage=storage, event_service=event_service)

    # Create Claude manager based on configuration
    if config.use_sdk:
        logger.info("Using Claude Python SDK integration")
        sdk_manager = ClaudeSDKManager(config)
        process_manager = None
    else:
        logger.info("Using Claude CLI subprocess integration")
        process_manager = ClaudeProcessManager(config)
        sdk_manager = None

    # Create main Claude integration facade
    claude_integration = ClaudeIntegration(
        config=config,
        process_manager=process_manager,
        sdk_manager=sdk_manager,
        session_manager=session_manager,
        tool_monitor=tool_monitor,
        permission_manager=permission_manager,
    )

    cli_integrations: Dict[str, Any] = {"claude": claude_integration}
    if config.enable_codex_cli:
        codex_cli_path = str(config.codex_cli_path or "").strip() or shutil.which(
            "codex"
        )
        if codex_cli_path:
            codex_config = config.model_copy(deep=True)
            codex_config.use_sdk = False
            codex_config.enable_mcp = False
            codex_config.claude_cli_path = codex_cli_path
            codex_config.claude_binary_path = codex_cli_path

            codex_session_storage = SQLiteSessionStorage(storage.db_manager)
            codex_session_manager = SessionManager(codex_config, codex_session_storage)
            codex_process_manager = ClaudeProcessManager(codex_config)
            codex_integration = ClaudeIntegration(
                config=codex_config,
                process_manager=codex_process_manager,
                sdk_manager=None,
                session_manager=codex_session_manager,
                tool_monitor=tool_monitor,
                permission_manager=permission_manager,
            )
            cli_integrations["codex"] = codex_integration
            logger.info("Codex CLI adapter enabled", codex_cli_path=codex_cli_path)
        else:
            logger.warning(
                "ENABLE_CODEX_CLI is true but codex binary not found; "
                "skip codex adapter"
            )

    # Initialize cc-switch provider manager
    cc_switch_manager = CCSwitchManager()
    if cc_switch_manager.is_available():
        logger.info("cc-switch available, running startup consistency check")
        await cc_switch_manager.startup_consistency_check()
    else:
        logger.info("cc-switch not available, provider switching disabled")

    # Create bot with all dependencies
    dependencies = {
        "auth_manager": auth_manager,
        "security_validator": security_validator,
        "rate_limiter": rate_limiter,
        "audit_logger": audit_logger,
        "claude_integration": claude_integration,
        "storage": storage,
        "permission_manager": permission_manager,
        "approval_service": approval_service,
        "session_lifecycle_service": session_lifecycle_service,
        "session_interaction_service": session_interaction_service,
        "event_service": event_service,
        "session_service": session_service,
        "cli_integrations": cli_integrations,
        "cc_switch_manager": cc_switch_manager,
    }

    bot = ClaudeCodeBot(config, dependencies)

    logger.info("Application components created successfully")

    return {
        "bot": bot,
        "claude_integration": claude_integration,
        "cli_integrations": cli_integrations,
        "storage": storage,
        "config": config,
    }


async def run_application(app: Dict[str, Any]) -> None:
    """Run the application with graceful shutdown handling."""
    logger = structlog.get_logger()
    bot: ClaudeCodeBot = app["bot"]
    claude_integration: ClaudeIntegration = app["claude_integration"]
    cli_integrations: Dict[str, Any] = app.get("cli_integrations") or {
        "claude": claude_integration
    }
    storage: Storage = app["storage"]
    process_started_monotonic = time.monotonic()

    # Set up signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        signal_name = (
            signal.Signals(signum).name
            if signum in {signal.SIGINT, signal.SIGTERM}
            else f"SIG_{signum}"
        )
        logger.info(
            "Shutdown signal received",
            signal=signum,
            signal_name=signal_name,
            pid=os.getpid(),
            ppid=os.getppid(),
            uptime_seconds=round(time.monotonic() - process_started_monotonic, 1),
        )
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Start the bot
        logger.info("Starting CLITG")

        # Run bot in background task
        bot_task = asyncio.create_task(bot.start())
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        # Wait for either bot completion or shutdown signal
        done, pending = await asyncio.wait(
            [bot_task, shutdown_task], return_when=asyncio.FIRST_COMPLETED
        )

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Re-raise bot task exception so the process exits with non-zero
        # code (allows systemd Restart=on-failure to kick in)
        if bot_task in done and not bot_task.cancelled():
            exc = bot_task.exception()
            if exc is not None:
                raise exc

    except Exception as e:
        logger.error("Application error", error=str(e))
        raise
    finally:
        # Graceful shutdown
        logger.info("Shutting down application")

        try:
            await bot.stop()
            shutdown_targets = []
            for integration in cli_integrations.values():
                if integration not in shutdown_targets:
                    shutdown_targets.append(integration)
            for integration in shutdown_targets:
                shutdown = getattr(integration, "shutdown", None)
                if shutdown is None:
                    continue
                result = shutdown()
                if asyncio.iscoroutine(result):
                    await result
            await storage.close()
        except Exception as e:
            logger.error("Error during shutdown", error=str(e))

        logger.info("Application shutdown complete")


async def main() -> None:
    """Main application entry point."""
    args = parse_args()
    setup_logging(debug=args.debug)

    logger = structlog.get_logger()
    logger.info("Starting CLITG", version=__version__)
    logger.info(
        "Process context",
        pid=os.getpid(),
        ppid=os.getppid(),
        cwd=str(Path.cwd()),
        python_executable=sys.executable,
    )

    try:
        # Load configuration
        from src.config import FeatureFlags, load_config

        config = load_config(config_file=args.config_file)
        features = FeatureFlags(config)

        logger.info(
            "Configuration loaded",
            environment="production" if config.is_production else "development",
            enabled_features=features.get_enabled_features(),
            debug=config.debug,
        )

        # Initialize bot and Claude integration
        app = await create_application(config)
        await run_application(app)

    except ConfigurationError as e:
        logger.error("Configuration error", error=str(e))
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error", error=str(e))
        sys.exit(1)


def run() -> None:
    """Synchronous entry point for setuptools."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
        sys.exit(0)


if __name__ == "__main__":
    run()
