"""Main Telegram bot class.

Features:
- Command registration
- Handler management
- Context injection
- Graceful shutdown
"""

import asyncio
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import structlog
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    TypeHandler,
    filters,
)

from ..claude.task_registry import TaskRegistry
from ..config.settings import Settings
from ..exceptions import ClaudeCodeTelegramError
from .features.registry import FeatureRegistry
from .utils.cli_engine import get_default_cli_engine
from .utils.command_menu import build_bot_commands_for_engine
from .utils.telegram_send import send_message_resilient
from .utils.update_dedupe import UpdateDedupeCache
from .utils.update_offset_store import UpdateOffsetStore

logger = structlog.get_logger()

_POLLING_WATCHDOG_INTERVAL_SECONDS = 2.0
_POLLING_RECOVERY_ERROR_THRESHOLD = 3
_POLLING_RESTART_COOLDOWN_SECONDS = 8.0
_POLLING_UPDATE_STALL_SECONDS = 1800.0
_POLLING_WATCHDOG_HEARTBEAT_INTERVAL_SECONDS = 60.0
_POLLING_HEALTH_PROBE_INTERVAL_SECONDS = 300.0
_POLLING_WATCHDOG_DELAY_WARNING_SECONDS = 15.0


class ClaudeCodeBot:
    """Main bot orchestrator."""

    def __init__(self, settings: Settings, dependencies: Dict[str, Any]):
        """Initialize bot with settings and dependencies."""
        self.settings = settings
        self.deps = dependencies
        self.app: Optional[Application] = None
        self.is_running = False
        self.feature_registry: Optional[FeatureRegistry] = None
        # Polling error tracking for rate-limited logging
        self._polling_error_count: int = 0
        self._polling_error_window_start: float = 0.0
        self._last_polling_error_log: float = 0.0
        self._polling_restart_requested: bool = False
        self._last_polling_restart_monotonic: float = 0.0
        self._started_monotonic: float = 0.0
        self._last_watchdog_tick_monotonic: float = 0.0
        self._last_watchdog_heartbeat_log_monotonic: float = 0.0
        self._last_health_probe_monotonic: float = 0.0
        self._watchdog_tick_count: int = 0
        self._last_update_monotonic: float = 0.0
        self._last_update_progress_monotonic: float = 0.0
        self._last_update_id: Optional[int] = None
        # Update dedupe and persisted offset tracking
        self._update_dedupe_cache = UpdateDedupeCache(ttl_seconds=300, max_size=5000)
        self._update_offset_store: Optional[UpdateOffsetStore] = None
        self._startup_min_update_id: Optional[int] = None

    async def initialize(self) -> None:
        """Initialize bot application."""
        logger.info("Initializing Telegram bot")

        # Create application
        builder = Application.builder()
        builder.token(self.settings.telegram_token_str)

        # Configure connection settings
        builder.connect_timeout(30)
        builder.read_timeout(30)
        builder.write_timeout(30)
        builder.pool_timeout(30)

        # Enable concurrent update processing so that permission button
        # callbacks can be handled while a Claude request is waiting for
        # user approval (without this the default serial processing causes
        # a deadlock where the callback_query update is queued behind the
        # blocked message update).
        builder.concurrent_updates(True)

        self.app = builder.build()

        # Initialize feature registry
        self.feature_registry = FeatureRegistry(
            config=self.settings,
            storage=self.deps.get("storage"),
            security=self.deps.get("security"),
        )

        # Add feature registry to dependencies
        self.deps["features"] = self.feature_registry

        # Initialize task registry for cancel support
        self.deps["task_registry"] = TaskRegistry()
        self._initialize_update_tracking()

        # Set bot commands for menu
        await self._set_bot_commands()

        # Register handlers
        self._register_handlers()

        # Add middleware
        self._add_middleware()

        # Set error handler
        self.app.add_error_handler(self._error_handler)

        # Schedule periodic image cleanup
        self._schedule_image_cleanup()

        # Check .gitignore for .claude-images/
        self._check_gitignore()

        logger.info("Bot initialization complete")

    async def _set_bot_commands(self) -> None:
        """Set bot command menu (non-fatal on failure)."""
        try:
            integrations = self.deps.get("cli_integrations")
            default_engine = get_default_cli_engine(
                integrations if isinstance(integrations, dict) else None
            )
            commands = build_bot_commands_for_engine(default_engine)
            await self.app.bot.set_my_commands(commands)
            logger.info(
                "Bot commands set",
                engine=default_engine,
                commands=[cmd.command for cmd in commands],
            )
        except Exception as e:
            logger.warning(
                "Failed to set bot commands, will retry on next startup",
                error=str(e),
                error_type=type(e).__name__,
            )

    def _register_handlers(self) -> None:
        """Register all command and message handlers."""
        from .handlers import callback, command, message

        # Global update guard (dedupe + stale offset filtering)
        self.app.add_handler(
            TypeHandler(Update, self._handle_update_guard),
            group=-10,
        )

        # Command handlers
        handlers = [
            ("start", command.start_command),
            ("help", command.help_command),
            ("new", command.new_session),
            ("continue", command.continue_session),
            ("end", command.end_session),
            ("ls", command.list_files),
            ("cd", command.change_directory),
            ("pwd", command.print_working_directory),
            ("projects", command.show_projects),
            ("context", command.session_status),
            ("status", command.status_command),
            ("engine", command.switch_engine),
            ("export", command.export_session),
            ("actions", command.quick_actions),
            ("git", command.git_command),
            ("cancel", command.cancel_task),
            ("resume", command.resume_command),
            ("model", command.model_command),
            ("codexdiag", command.codex_diag_command),
            ("provider", command.switch_provider),
        ]

        for cmd, handler in handlers:
            self.app.add_handler(CommandHandler(cmd, self._inject_deps(handler)))

        # Message handlers with priority groups
        self.app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._inject_deps(message.handle_text_message),
            ),
            group=10,
        )

        self.app.add_handler(
            MessageHandler(
                filters.Document.ALL, self._inject_deps(message.handle_document)
            ),
            group=10,
        )

        self.app.add_handler(
            MessageHandler(filters.PHOTO, self._inject_deps(message.handle_photo)),
            group=10,
        )

        # Message reaction handler (emoji reactions on messages)
        self.app.add_handler(
            MessageReactionHandler(
                self._inject_deps(message.handle_message_reaction),
                message_reaction_types=(
                    MessageReactionHandler.MESSAGE_REACTION_UPDATED
                    | MessageReactionHandler.MESSAGE_REACTION_COUNT_UPDATED
                ),
            ),
            group=10,
        )
        # Generic fallback for reaction updates in case specialized handler misses.
        self.app.add_handler(
            TypeHandler(
                Update,
                self._inject_deps(message.handle_reaction_update_fallback),
            ),
            group=10,
        )

        # Callback query handler
        self.app.add_handler(
            CallbackQueryHandler(self._inject_deps(callback.handle_callback_query))
        )

        logger.info("Bot handlers registered")

    def _build_update_offset_state_file(self) -> Optional[Path]:
        """Build persisted update offset state file path."""
        approved_directory = getattr(self.settings, "approved_directory", None)
        if not isinstance(approved_directory, Path):
            return None
        return approved_directory / "data/state/telegram/update-offset.json"

    def _initialize_update_tracking(self) -> None:
        """Initialize update dedupe and persisted offset tracking."""
        state_file = self._build_update_offset_state_file()
        if state_file is None:
            logger.warning(
                "Approved directory missing, update offset persistence disabled"
            )
            self._update_offset_store = None
            self._startup_min_update_id = None
            return

        store = UpdateOffsetStore(state_file)
        self._update_offset_store = store

        try:
            last_update_id = store.load()
        except Exception as exc:
            logger.warning(
                "Failed to load Telegram update offset, "
                "starting without persisted offset",
                state_file=str(state_file),
                error=str(exc),
            )
            self._startup_min_update_id = None
            return

        self._startup_min_update_id = (
            last_update_id + 1 if isinstance(last_update_id, int) else None
        )
        logger.info(
            "Telegram update tracking initialized",
            state_file=str(state_file),
            last_update_id=last_update_id,
            startup_min_update_id=self._startup_min_update_id,
        )

    async def _handle_update_guard(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Drop stale/duplicate updates before entering business handlers."""
        self._last_update_progress_monotonic = time.monotonic()

        update_id = getattr(update, "update_id", None)
        if not isinstance(update_id, int):
            return

        if (
            self._startup_min_update_id is not None
            and update_id < self._startup_min_update_id
        ):
            logger.debug(
                "Skipping stale Telegram update below persisted offset",
                update_id=update_id,
                startup_min_update_id=self._startup_min_update_id,
            )
            raise ApplicationHandlerStop

        if self._update_dedupe_cache.check_and_mark(update_id):
            logger.debug("Skipping duplicate Telegram update", update_id=update_id)
            raise ApplicationHandlerStop

        now = asyncio.get_running_loop().time()
        self._last_update_monotonic = now
        self._last_update_id = update_id

        if self._update_offset_store is not None:
            try:
                self._update_offset_store.record(update_id)
            except Exception as exc:
                logger.warning(
                    "Failed to persist Telegram update offset",
                    update_id=update_id,
                    error=str(exc),
                )

    def _inject_deps(self, handler: Callable) -> Callable:
        """Inject dependencies into handlers."""

        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            # Add dependencies to context
            for key, value in self.deps.items():
                context.bot_data[key] = value

            # Add settings
            context.bot_data["settings"] = self.settings

            return await handler(update, context)

        return wrapped

    def _add_middleware(self) -> None:
        """Add middleware to application."""
        from .middleware.auth import auth_middleware
        from .middleware.security import security_middleware

        app = self.app
        if app is None:
            raise ClaudeCodeTelegramError("Telegram application is not initialized")

        # Middleware runs in order of group numbers (lower = earlier)
        # Security middleware first (validate inputs)
        app.add_handler(
            MessageHandler(
                filters.ALL, self._create_middleware_handler(security_middleware)
            ),
            group=-3,
        )

        # Authentication second
        app.add_handler(
            MessageHandler(
                filters.ALL, self._create_middleware_handler(auth_middleware)
            ),
            group=-2,
        )

        logger.info("Middleware added to bot")

    def _create_middleware_handler(self, middleware_func: Callable) -> Callable:
        """Create middleware handler that injects dependencies."""

        async def middleware_wrapper(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ):
            # Inject dependencies into context
            for key, value in self.deps.items():
                context.bot_data[key] = value
            context.bot_data["settings"] = self.settings

            # Create a dummy handler that does nothing.
            # Middleware performs all pre-handler checks itself.
            async def dummy_handler(event, data):
                return None

            # Call middleware with Telegram-style parameters
            return await middleware_func(dummy_handler, update, context.bot_data)

        return middleware_wrapper

    def _schedule_image_cleanup(self) -> None:
        """Register periodic image cleanup job."""
        if not self.app.job_queue:
            logger.warning("Job queue not available, skipping image cleanup scheduling")
            return

        from .features.image_handler import ImageHandler

        async def _cleanup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
            deleted = ImageHandler.cleanup_old_images(
                self.settings.approved_directory,
                self.settings.image_cleanup_max_age_hours,
            )
            if deleted:
                logger.info("Image cleanup completed", deleted=deleted)

        self.app.job_queue.run_repeating(
            _cleanup_job, interval=3600, first=60, name="image_cleanup"
        )
        logger.info("Image cleanup job scheduled", interval_hours=1)

    async def _finalize_running_tasks_before_shutdown(self) -> None:
        """Mark running tasks as interrupted and clear stale cancel buttons."""
        if not self.app:
            return
        task_registry = self.deps.get("task_registry")
        if not isinstance(task_registry, TaskRegistry):
            return

        running_tasks = await task_registry.list_running()
        if not running_tasks:
            return

        logger.info(
            "Finalizing running tasks before shutdown", count=len(running_tasks)
        )

        for active in running_tasks:
            try:
                await task_registry.cancel(active.user_id, scope_key=active.scope_key)
            except Exception as exc:
                logger.warning(
                    "Failed to cancel running task during shutdown",
                    user_id=active.user_id,
                    scope_key=active.scope_key,
                    error=str(exc),
                )

            if active.chat_id and active.progress_message_id:
                try:
                    await self.app.bot.edit_message_text(
                        chat_id=active.chat_id,
                        message_id=active.progress_message_id,
                        text="⚠️ 服务已重启，本次任务已中断。请重新发送消息继续。",
                        reply_markup=None,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to mark progress message as interrupted",
                        chat_id=active.chat_id,
                        message_id=active.progress_message_id,
                        error=str(exc),
                    )
                    try:
                        await self.app.bot.edit_message_reply_markup(
                            chat_id=active.chat_id,
                            message_id=active.progress_message_id,
                            reply_markup=None,
                        )
                    except Exception:
                        pass

            await task_registry.remove(active.user_id, scope_key=active.scope_key)

    def _check_gitignore(self) -> None:
        """Warn if .claude-images/ is not in .gitignore."""
        gitignore = self.settings.approved_directory / ".gitignore"
        if not gitignore.is_file():
            logger.warning(
                ".gitignore not found, consider adding .claude-images/ to it",
                dir=str(self.settings.approved_directory),
            )
            return
        try:
            content = gitignore.read_text(encoding="utf-8")
            if ".claude-images" not in content:
                logger.warning(
                    ".claude-images/ not in .gitignore, "
                    "uploaded images may be committed",
                    gitignore=str(gitignore),
                )
        except OSError:
            pass

    def _reset_polling_recovery_state(self) -> None:
        """Reset polling network error counters after successful recovery."""
        self._polling_error_count = 0
        self._polling_error_window_start = 0.0
        self._last_polling_error_log = 0.0
        self._polling_restart_requested = False
        self._last_update_progress_monotonic = time.monotonic()

    def _emit_polling_watchdog_heartbeat(
        self, *, now: float, updater_running: bool
    ) -> None:
        """Emit periodic watchdog heartbeat logs for runtime diagnosis."""
        if (
            now - self._last_watchdog_heartbeat_log_monotonic
            < _POLLING_WATCHDOG_HEARTBEAT_INTERVAL_SECONDS
        ):
            return

        self._last_watchdog_heartbeat_log_monotonic = now
        uptime_seconds = (
            round(now - self._started_monotonic, 1)
            if self._started_monotonic > 0
            else None
        )
        last_update_age_seconds = (
            round(now - self._last_update_monotonic, 1)
            if self._last_update_monotonic > 0
            else None
        )
        logger.info(
            "Polling watchdog heartbeat",
            updater_running=updater_running,
            polling_restart_requested=self._polling_restart_requested,
            polling_error_count=self._polling_error_count,
            watchdog_tick_count=self._watchdog_tick_count,
            last_update_id=self._last_update_id,
            last_update_age_seconds=last_update_age_seconds,
            uptime_seconds=uptime_seconds,
        )

    async def _run_polling_health_probe(self, *, now: float) -> None:
        """Periodically probe Telegram API and emit explicit health logs."""
        if (
            now - self._last_health_probe_monotonic
            < _POLLING_HEALTH_PROBE_INTERVAL_SECONDS
        ):
            return
        self._last_health_probe_monotonic = now

        if not self.app:
            return
        bot = getattr(self.app, "bot", None)
        if bot is None:
            return

        try:
            me = await bot.get_me()
            logger.info(
                "Polling health probe succeeded",
                bot_id=getattr(me, "id", None),
                bot_username=getattr(me, "username", None),
            )
        except Exception as exc:
            logger.warning(
                "Polling health probe failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _start_polling(self) -> None:
        """Start Telegram polling with shared options."""
        if not self.app:
            raise ClaudeCodeTelegramError("Telegram application is not initialized")

        updater = getattr(self.app, "updater", None)
        if updater is None:
            raise ClaudeCodeTelegramError("Telegram updater is not available")

        await updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
            bootstrap_retries=10,
            error_callback=self._polling_error_callback,
        )

    async def _restart_polling(self, *, reason: str) -> bool:
        """Restart polling loop after network/proxy disruptions."""
        if not self.app:
            return False

        updater = getattr(self.app, "updater", None)
        if updater is None:
            logger.error(
                "Cannot restart polling: updater is unavailable", reason=reason
            )
            return False

        now = asyncio.get_running_loop().time()
        if (
            now - self._last_polling_restart_monotonic
            < _POLLING_RESTART_COOLDOWN_SECONDS
        ):
            logger.debug(
                "Skip polling restart due to cooldown",
                reason=reason,
                cooldown_seconds=_POLLING_RESTART_COOLDOWN_SECONDS,
            )
            return False

        self._last_polling_restart_monotonic = now
        logger.warning(
            "Attempting polling self-recovery",
            reason=reason,
            updater_running=updater.running,
            polling_error_count=self._polling_error_count,
            polling_restart_requested=self._polling_restart_requested,
            last_update_id=self._last_update_id,
        )

        try:
            if updater.running:
                await updater.stop()
            await self._start_polling()
        except Exception as exc:
            self._polling_restart_requested = True
            logger.error(
                "Polling self-recovery failed",
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

        self._reset_polling_recovery_state()
        logger.info(
            "Polling self-recovery succeeded",
            reason=reason,
            updater_running=updater.running,
            last_update_id=self._last_update_id,
        )
        return True

    async def _polling_watchdog_tick(self) -> None:
        """Watch polling status and trigger self-recovery when needed."""
        if getattr(self.settings, "webhook_url", None) or not self.app:
            return

        now = asyncio.get_running_loop().time()
        tick_delay_seconds: Optional[float] = None
        if self._last_watchdog_tick_monotonic > 0:
            tick_delay_seconds = now - self._last_watchdog_tick_monotonic
            if tick_delay_seconds > _POLLING_WATCHDOG_DELAY_WARNING_SECONDS:
                logger.warning(
                    "Polling watchdog tick delayed",
                    observed_delay_seconds=round(tick_delay_seconds, 1),
                    expected_interval_seconds=_POLLING_WATCHDOG_INTERVAL_SECONDS,
                )
        self._last_watchdog_tick_monotonic = now
        self._watchdog_tick_count += 1

        updater = getattr(self.app, "updater", None)
        if updater is None:
            return

        updater_running = bool(updater.running)
        self._emit_polling_watchdog_heartbeat(
            now=now,
            updater_running=updater_running,
        )
        await self._run_polling_health_probe(now=now)

        if (
            tick_delay_seconds
            and tick_delay_seconds > _POLLING_WATCHDOG_DELAY_WARNING_SECONDS
        ):
            logger.info(
                "Watchdog delay context",
                polling_restart_requested=self._polling_restart_requested,
                polling_error_count=self._polling_error_count,
                last_update_id=self._last_update_id,
                last_update_age_seconds=(
                    round(now - self._last_update_monotonic, 1)
                    if self._last_update_monotonic > 0
                    else None
                ),
            )

        if not updater.running:
            await self._restart_polling(reason="updater_not_running")
            return

        if self._polling_restart_requested:
            await self._restart_polling(reason="network_error_threshold")
            return

        now = time.monotonic()
        if self._last_update_progress_monotonic <= 0:
            self._last_update_progress_monotonic = now
            return

        if now - self._last_update_progress_monotonic >= _POLLING_UPDATE_STALL_SECONDS:
            logger.warning(
                "Polling watchdog detected stalled update progression",
                stall_seconds=round(now - self._last_update_progress_monotonic, 2),
                threshold_seconds=_POLLING_UPDATE_STALL_SECONDS,
            )
            await self._restart_polling(reason="update_stall_watchdog")

    async def start(self) -> None:
        """Start the bot."""
        if self.is_running:
            logger.warning("Bot is already running")
            return

        await self.initialize()

        logger.info(
            "Starting bot", mode="webhook" if self.settings.webhook_url else "polling"
        )

        try:
            self.is_running = True
            now = asyncio.get_running_loop().time()
            self._started_monotonic = now
            self._last_watchdog_tick_monotonic = now
            self._last_watchdog_heartbeat_log_monotonic = 0.0
            self._last_health_probe_monotonic = 0.0
            self._watchdog_tick_count = 0
            self._last_update_monotonic = 0.0
            self._last_update_id = None

            if self.settings.webhook_url:
                # Webhook mode
                await self.app.run_webhook(
                    listen="0.0.0.0",
                    port=self.settings.webhook_port,
                    url_path=self.settings.webhook_path,
                    webhook_url=self.settings.webhook_url,
                    drop_pending_updates=False,
                    allowed_updates=Update.ALL_TYPES,
                )
            else:
                # Polling mode - initialize and start polling manually
                await self.app.initialize()
                await self.app.start()
                await self._start_polling()
                self._reset_polling_recovery_state()

                # Keep running until manually stopped
                while self.is_running:
                    await asyncio.sleep(_POLLING_WATCHDOG_INTERVAL_SECONDS)
                    await self._polling_watchdog_tick()
        except Exception as e:
            logger.error("Error running bot", error=str(e))
            raise ClaudeCodeTelegramError(f"Failed to start bot: {str(e)}") from e
        finally:
            self.is_running = False

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        if not self.is_running:
            logger.warning("Bot is not running")
            return

        now = asyncio.get_running_loop().time()
        logger.info(
            "Stopping bot",
            uptime_seconds=(
                round(now - self._started_monotonic, 1)
                if self._started_monotonic > 0
                else None
            ),
            last_update_id=self._last_update_id,
            last_update_age_seconds=(
                round(now - self._last_update_monotonic, 1)
                if self._last_update_monotonic > 0
                else None
            ),
        )

        try:
            self.is_running = False  # Stop the main loop first

            # Best effort: notify users and clear stale "Cancel" buttons
            # before the app is torn down.
            await self._finalize_running_tasks_before_shutdown()

            # Shutdown feature registry
            if self.feature_registry:
                self.feature_registry.shutdown()

            if self._update_offset_store is not None:
                try:
                    self._update_offset_store.flush(force=True)
                except Exception as exc:
                    logger.warning(
                        "Failed to flush Telegram update offset on shutdown",
                        error=str(exc),
                    )

            if self.app:
                # Stop the updater if it's running
                if self.app.updater.running:
                    await self.app.updater.stop()

                # Stop the application
                await self.app.stop()
                await self.app.shutdown()

            logger.info("Bot stopped successfully")
        except Exception as e:
            logger.error("Error stopping bot", error=str(e))
            raise ClaudeCodeTelegramError(f"Failed to stop bot: {str(e)}") from e

    def _polling_error_callback(self, error: Exception) -> None:
        """Handle network errors during polling (sync callback, required by PTB)."""
        import time

        now = time.monotonic()

        # Reset sliding window (60s window)
        if now - self._polling_error_window_start > 60:
            self._polling_error_count = 0
            self._polling_error_window_start = now

        self._polling_error_count += 1

        if (
            self._polling_error_count >= _POLLING_RECOVERY_ERROR_THRESHOLD
            and not self._polling_restart_requested
        ):
            self._polling_restart_requested = True
            logger.warning(
                "Polling self-recovery flagged due to repeated network errors",
                error_count_in_window=self._polling_error_count,
                threshold=_POLLING_RECOVERY_ERROR_THRESHOLD,
            )

        # Rate limit: at most one log entry per 30 seconds
        if now - self._last_polling_error_log < 30:
            return

        self._last_polling_error_log = now
        log_fn = logger.error if self._polling_error_count > 5 else logger.warning
        log_fn(
            "Polling network error (PTB will retry automatically)",
            error=str(error),
            error_type=type(error).__name__,
            error_count_in_window=self._polling_error_count,
        )

    async def _reply_update_message_resilient(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
    ) -> Any:
        """Reply to effective message with fallback to resilient send helper."""
        message = getattr(update, "effective_message", None)
        if message is None:
            return None

        try:
            return await message.reply_text(text)
        except Exception:
            bot = getattr(context, "bot", None)
            if bot is None and self.app is not None:
                bot = self.app.bot

            chat = getattr(update, "effective_chat", None)
            chat_id = getattr(chat, "id", None)
            if bot is None or not isinstance(chat_id, int):
                raise

            return await send_message_resilient(
                bot=bot,
                chat_id=chat_id,
                text=text,
                reply_to_message_id=getattr(message, "message_id", None),
                message_thread_id=getattr(message, "message_thread_id", None),
                chat_type=getattr(chat, "type", None),
            )

    async def _error_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle errors globally."""
        error = context.error
        logger.error(
            "Global error handler triggered",
            error=str(error),
            update_type=type(update).__name__ if update else None,
            user_id=(
                update.effective_user.id if update and update.effective_user else None
            ),
        )

        # Determine error message for user
        from ..exceptions import (
            AuthenticationError,
            ConfigurationError,
            RateLimitExceeded,
            SecurityError,
        )

        error_messages = {
            AuthenticationError: (
                "🔒 Authentication required. Please contact the administrator."
            ),
            SecurityError: (
                "🛡️ Security violation detected. This incident has been logged."
            ),
            RateLimitExceeded: (
                "⏱️ Rate limit exceeded. Please wait before sending more messages."
            ),
            ConfigurationError: (
                "⚙️ Configuration error. Please contact the administrator."
            ),
            asyncio.TimeoutError: (
                "⏰ Operation timed out. Please try again with a simpler request."
            ),
        }

        error_type = type(error)
        user_message = error_messages.get(
            error_type, "❌ An unexpected error occurred. Please try again."
        )

        # Try to notify user
        if update and update.effective_message:
            try:
                await self._reply_update_message_resilient(
                    update, context, user_message
                )
            except Exception:
                logger.exception("Failed to send error message to user")

        # Log to audit system if available
        from ..security.audit import AuditLogger

        audit_logger: Optional[AuditLogger] = context.bot_data.get("audit_logger")
        if audit_logger and update and update.effective_user:
            try:
                await audit_logger.log_security_violation(
                    user_id=update.effective_user.id,
                    violation_type="system_error",
                    details=f"Error type: {error_type.__name__}, Message: {str(error)}",
                    severity="medium",
                )
            except Exception:
                logger.exception("Failed to log error to audit system")

    async def get_bot_info(self) -> Dict[str, Any]:
        """Get bot information."""
        if not self.app:
            return {"status": "not_initialized"}

        try:
            me = await self.app.bot.get_me()
            return {
                "status": "running" if self.is_running else "initialized",
                "username": me.username,
                "first_name": me.first_name,
                "id": me.id,
                "can_join_groups": me.can_join_groups,
                "can_read_all_group_messages": me.can_read_all_group_messages,
                "supports_inline_queries": me.supports_inline_queries,
                "webhook_url": self.settings.webhook_url,
                "webhook_port": (
                    self.settings.webhook_port if self.settings.webhook_url else None
                ),
            }
        except Exception as e:
            logger.error("Failed to get bot info", error=str(e))
            return {"status": "error", "error": str(e)}

    async def health_check(self) -> bool:
        """Perform health check."""
        try:
            if not self.app:
                return False

            # Try to get bot info
            await self.app.bot.get_me()
            return True
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return False
