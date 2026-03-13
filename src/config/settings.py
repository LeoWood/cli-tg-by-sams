"""Configuration management using Pydantic Settings.

Features:
- Environment variable loading
- Type validation
- Default values
- Computed properties
- Environment-specific settings
"""

import json
from pathlib import Path
from typing import Any, List, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.utils.constants import (
    DEFAULT_CLAUDE_MAX_COST_PER_USER,
    DEFAULT_CLAUDE_MAX_TURNS,
    DEFAULT_CLAUDE_TIMEOUT_SECONDS,
    DEFAULT_DATABASE_URL,
    DEFAULT_MAX_SESSIONS_PER_USER,
    DEFAULT_RATE_LIMIT_BURST,
    DEFAULT_RATE_LIMIT_REQUESTS,
    DEFAULT_RATE_LIMIT_WINDOW,
    DEFAULT_SESSION_TIMEOUT_HOURS,
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Bot settings
    telegram_bot_token: SecretStr = Field(
        ..., description="Telegram bot token from BotFather"
    )
    telegram_bot_username: str = Field(..., description="Bot username without @")

    # Security
    approved_directory: Path = Field(..., description="Base directory for projects")
    allowed_users: Optional[List[int]] = Field(
        None, description="Allowed Telegram user IDs"
    )
    enable_token_auth: bool = Field(
        False, description="Enable token-based authentication"
    )
    auth_token_secret: Optional[SecretStr] = Field(
        None, description="Secret for auth tokens"
    )

    # Claude settings
    claude_binary_path: Optional[str] = Field(
        None, description="Path to Claude CLI binary (deprecated)"
    )
    claude_cli_path: Optional[str] = Field(
        None, description="Path to Claude CLI executable"
    )
    claude_setting_sources: Optional[List[str]] = Field(
        None,
        description=(
            "Optional setting sources for Claude SDK "
            "(example: user,project,local). Leave empty to use CLI defaults."
        ),
    )
    anthropic_api_key: Optional[SecretStr] = Field(
        None,
        description="Anthropic API key for Claude SDK (optional if logged into Claude CLI)",
    )
    claude_model: str = Field(
        "claude-3-5-sonnet-20241022", description="Claude model to use"
    )
    claude_max_turns: int = Field(
        DEFAULT_CLAUDE_MAX_TURNS, description="Max conversation turns"
    )
    claude_timeout_seconds: int = Field(
        DEFAULT_CLAUDE_TIMEOUT_SECONDS, description="Claude timeout"
    )
    claude_max_cost_per_user: float = Field(
        DEFAULT_CLAUDE_MAX_COST_PER_USER, description="Max cost per user"
    )
    use_sdk: bool = Field(True, description="Use Python SDK instead of CLI subprocess")
    enable_codex_cli: bool = Field(
        False,
        description="Enable Codex CLI adapter (subprocess mode)",
    )
    enable_gemini_cli: bool = Field(
        False,
        description="Enable Gemini CLI adapter (subprocess mode)",
    )
    codex_enable_mcp: bool = Field(
        False,
        description="Enable MCP servers for Codex CLI sessions",
    )
    codex_cli_path: Optional[str] = Field(
        None,
        description="Path to Codex CLI executable",
    )
    gemini_cli_path: Optional[str] = Field(
        None,
        description="Path to Gemini CLI executable",
    )
    gemini_approval_mode: str = Field(
        "yolo",
        description="Gemini CLI approval mode for headless subprocess runs",
    )
    claude_allowed_tools: Optional[List[str]] = Field(
        default=[
            "Read",
            "Write",
            "Edit",
            "Bash",
            "Glob",
            "Grep",
            "LS",
            "Task",
            "MultiEdit",
            "NotebookRead",
            "NotebookEdit",
            "WebFetch",
            "TodoRead",
            "TodoWrite",
            "WebSearch",
            "Skill",
            "AskUserQuestion",
        ],
        description="List of allowed Claude tools",
    )
    claude_disallowed_tools: Optional[List[str]] = Field(
        default=["git commit", "git push"],
        description="List of explicitly disallowed Claude tools/commands",
    )

    # Rate limiting
    rate_limit_requests: int = Field(
        DEFAULT_RATE_LIMIT_REQUESTS, description="Requests per window"
    )
    rate_limit_window: int = Field(
        DEFAULT_RATE_LIMIT_WINDOW, description="Rate limit window seconds"
    )
    rate_limit_burst: int = Field(
        DEFAULT_RATE_LIMIT_BURST, description="Burst capacity"
    )

    # Storage
    database_url: str = Field(
        DEFAULT_DATABASE_URL, description="Database connection URL"
    )
    session_timeout_hours: int = Field(
        DEFAULT_SESSION_TIMEOUT_HOURS, description="Session timeout"
    )
    session_timeout_minutes: int = Field(
        default=120,
        description="Session timeout in minutes",
        ge=10,
        le=1440,  # Max 24 hours
    )
    max_sessions_per_user: int = Field(
        DEFAULT_MAX_SESSIONS_PER_USER, description="Max concurrent sessions"
    )

    # Features
    enable_mcp: bool = Field(False, description="Enable Model Context Protocol")
    mcp_config_path: Optional[Path] = Field(
        None, description="MCP configuration file path"
    )
    enable_git_integration: bool = Field(True, description="Enable git commands")
    enable_file_uploads: bool = Field(True, description="Enable file upload handling")
    enable_quick_actions: bool = Field(True, description="Enable quick action buttons")
    auto_delivery_directory: Optional[Path] = Field(
        None,
        description=(
            "Preferred directory for auto-delivered generated files/images. "
            "Relative values are resolved against APPROVED_DIRECTORY."
        ),
    )
    auto_delivery_allowed_directories: Optional[List[Path]] = Field(
        None,
        description=(
            "Extra allowed roots (comma-separated) for auto-delivery path checks. "
            "Relative values are resolved against APPROVED_DIRECTORY."
        ),
    )
    auto_delivery_require_directory: bool = Field(
        False,
        description=(
            "Require generated files/images to be under AUTO_DELIVERY_DIRECTORY "
            "before auto-delivery."
        ),
    )
    image_cleanup_max_age_hours: int = Field(
        24, description="Max age in hours for uploaded images before cleanup"
    )
    resume_scan_cache_ttl_seconds: int = Field(
        30,
        description="TTL for /resume desktop session scan cache",
        ge=0,
        le=3600,
    )
    resume_history_preview_count: int = Field(
        6,
        description="Number of recent messages to show after resuming a session",
        ge=0,
        le=20,
    )
    stream_render_debounce_ms: int = Field(
        1000,
        description="Debounce interval for streaming progress message updates",
        ge=0,
        le=5000,
    )
    stream_render_min_edit_interval_ms: int = Field(
        1000,
        description="Minimum interval between Telegram progress message edits",
        ge=0,
        le=10000,
    )
    inbound_queue_max_per_scope: int = Field(
        20,
        description="Max queued inbound tasks per scope",
        ge=1,
        le=200,
    )
    status_context_probe_ttl_seconds: int = Field(
        0,
        description="TTL for /context precise /context probe cache (0 disables cache)",
        ge=0,
        le=600,
    )
    status_context_probe_timeout_seconds: int = Field(
        45,
        description="Timeout for /context precise /context probe (seconds)",
        ge=5,
        le=300,
    )
    telegram_connect_timeout_seconds: float = Field(
        30.0,
        description="Telegram API connect timeout (seconds)",
        ge=1.0,
        le=300.0,
    )
    telegram_read_timeout_seconds: float = Field(
        30.0,
        description="Telegram API read timeout (seconds)",
        ge=1.0,
        le=300.0,
    )
    telegram_write_timeout_seconds: float = Field(
        30.0,
        description="Telegram API write timeout (seconds)",
        ge=1.0,
        le=300.0,
    )
    telegram_pool_timeout_seconds: float = Field(
        30.0,
        description="Telegram API connection pool wait timeout (seconds)",
        ge=1.0,
        le=300.0,
    )
    telegram_connection_pool_size: int = Field(
        64,
        description="Telegram API connection pool size for general requests",
        ge=8,
        le=512,
    )
    telegram_get_updates_read_timeout_seconds: float = Field(
        50.0,
        description="getUpdates long-poll read timeout (seconds)",
        ge=5.0,
        le=300.0,
    )
    telegram_get_updates_pool_timeout_seconds: float = Field(
        30.0,
        description="getUpdates pool wait timeout (seconds)",
        ge=1.0,
        le=300.0,
    )
    telegram_get_updates_connection_pool_size: int = Field(
        16,
        description="Dedicated connection pool size for getUpdates",
        ge=2,
        le=128,
    )
    telegram_user_data_persistence_path: Optional[Path] = Field(
        Path("data/telegram-user-data.pkl"),
        description=(
            "Pickle file path for Telegram user_data persistence; "
            "set empty value to disable"
        ),
    )
    polling_update_stall_seconds: float = Field(
        60.0,
        description=(
            "Polling stall watchdog threshold in seconds while Telegram reports "
            "pending updates; set 0 to disable update-stall auto restart"
        ),
        ge=0.0,
        le=86400.0,
    )
    polling_pending_update_stall_seconds: float = Field(
        120.0,
        description=(
            "Polling pending-update stall threshold in seconds; "
            "auto restart when Telegram queue has pending updates but bot consumes none"
        ),
        ge=0.0,
        le=86400.0,
    )
    polling_restart_timeout_seconds: float = Field(
        20.0,
        description=(
            "Timeout for a single polling self-recovery attempt; "
            "on timeout the bot escalates to process restart/fail-fast"
        ),
        ge=1.0,
        le=300.0,
    )
    telegram_noncritical_failure_threshold: int = Field(
        3,
        description=(
            "Consecutive Telegram network failures before disabling non-critical "
            "updates such as typing heartbeat and progress edits"
        ),
        ge=1,
        le=20,
    )
    telegram_noncritical_cooldown_seconds: float = Field(
        60.0,
        description=(
            "Cooldown window after Telegram non-critical transport is disabled; "
            "non-critical updates are skipped during this period"
        ),
        ge=1.0,
        le=3600.0,
    )
    metrics_enabled: bool = Field(
        False,
        description="Enable local read-only metrics HTTP endpoint",
    )
    metrics_host: str = Field(
        "127.0.0.1",
        description="Listen host for the metrics HTTP endpoint",
    )
    metrics_port: int = Field(
        9464,
        description="Listen port for the metrics HTTP endpoint",
        ge=1,
        le=65535,
    )

    # Monitoring
    log_level: str = Field("INFO", description="Logging level")
    enable_telemetry: bool = Field(False, description="Enable anonymous telemetry")
    sentry_dsn: Optional[str] = Field(None, description="Sentry DSN for error tracking")

    # Development
    debug: bool = Field(False, description="Enable debug mode")
    development_mode: bool = Field(False, description="Enable development features")

    # Webhook settings (optional)
    webhook_url: Optional[str] = Field(None, description="Webhook URL for bot")
    webhook_port: int = Field(8443, description="Webhook port")
    webhook_path: str = Field("/webhook", description="Webhook path")

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    @field_validator("allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, v: Any) -> Optional[List[int]]:
        """Parse comma-separated user IDs."""
        if v is None:
            return None
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            return [int(uid.strip()) for uid in v.split(",") if uid.strip()]
        if isinstance(v, list):
            return [int(uid) for uid in v]
        return v  # type: ignore[no-any-return]

    @field_validator("claude_allowed_tools", mode="before")
    @classmethod
    def parse_claude_allowed_tools(cls, v: Any) -> Optional[List[str]]:
        """Parse comma-separated tool names."""
        if v is None:
            return None
        if isinstance(v, str):
            return [tool.strip() for tool in v.split(",") if tool.strip()]
        if isinstance(v, list):
            return [str(tool) for tool in v]
        return v  # type: ignore[no-any-return]

    @field_validator("claude_setting_sources", mode="before")
    @classmethod
    def parse_claude_setting_sources(cls, v: Any) -> Optional[List[str]]:
        """Parse optional Claude SDK setting_sources."""
        if v is None:
            return None
        if isinstance(v, str):
            sources = [item.strip() for item in v.split(",") if item.strip()]
            return sources or None
        if isinstance(v, list):
            sources = [str(item).strip() for item in v if str(item).strip()]
            return sources or None
        return v  # type: ignore[no-any-return]

    @field_validator("telegram_user_data_persistence_path", mode="before")
    @classmethod
    def parse_telegram_user_data_persistence_path(cls, v: Any) -> Optional[Path]:
        """Allow empty string to disable Telegram user_data persistence."""
        if v is None:
            return None
        if isinstance(v, str):
            normalized = v.strip()
            if not normalized:
                return None
            return Path(normalized)
        if isinstance(v, Path):
            return v
        return Path(str(v))

    @field_validator("auto_delivery_directory", mode="before")
    @classmethod
    def parse_auto_delivery_directory(cls, v: Any) -> Optional[Path]:
        """Allow empty value and normalize auto delivery directory input."""
        if v is None:
            return None
        if isinstance(v, str):
            normalized = v.strip()
            if not normalized:
                return None
            return Path(normalized)
        if isinstance(v, Path):
            return v
        return Path(str(v))

    @field_validator("auto_delivery_allowed_directories", mode="before")
    @classmethod
    def parse_auto_delivery_allowed_directories(cls, v: Any) -> Optional[List[Path]]:
        """Parse comma-separated extra auto delivery roots."""
        if v is None:
            return None
        if isinstance(v, str):
            raw_items = [item.strip() for item in v.split(",") if item.strip()]
            return [Path(item) for item in raw_items] or None
        if isinstance(v, list):
            parsed: list[Path] = []
            for item in v:
                normalized = str(item).strip()
                if not normalized:
                    continue
                parsed.append(Path(normalized))
            return parsed or None
        return v  # type: ignore[no-any-return]

    @field_validator("approved_directory")
    @classmethod
    def validate_approved_directory(cls, v: Any) -> Path:
        """Ensure approved directory exists and is absolute."""
        if isinstance(v, str):
            v = Path(v)

        path = v.resolve()
        if not path.exists():
            raise ValueError(f"Approved directory does not exist: {path}")
        if not path.is_dir():
            raise ValueError(f"Approved directory is not a directory: {path}")
        return path  # type: ignore[no-any-return]

    @field_validator("mcp_config_path", mode="before")
    @classmethod
    def validate_mcp_config(cls, v: Any, info: Any) -> Optional[Path]:
        """Validate MCP configuration path if MCP is enabled."""
        if not v:
            return v  # type: ignore[no-any-return]
        if isinstance(v, str):
            v = Path(v)
        if not v.exists():
            raise ValueError(f"MCP config file does not exist: {v}")
        # Validate that the file contains valid JSON with mcpServers
        try:
            with open(v) as f:
                config_data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"MCP config file is not valid JSON: {e}")
        if not isinstance(config_data, dict):
            raise ValueError("MCP config file must contain a JSON object")
        if "mcpServers" not in config_data:
            raise ValueError(
                "MCP config file must contain a 'mcpServers' key. "
                'Expected format: {"mcpServers": {"server-name": {"command": "...", ...}}}'
            )
        if not isinstance(config_data["mcpServers"], dict):
            raise ValueError(
                "'mcpServers' must be an object mapping server names to configurations"
            )
        if not config_data["mcpServers"]:
            raise ValueError(
                "'mcpServers' must contain at least one server configuration"
            )
        return v  # type: ignore[no-any-return]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: Any) -> str:
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()  # type: ignore[no-any-return]

    @model_validator(mode="after")
    def validate_cross_field_dependencies(self) -> "Settings":
        """Validate dependencies between fields."""
        # Check auth token requirements
        if self.enable_token_auth and not self.auth_token_secret:
            raise ValueError(
                "auth_token_secret required when enable_token_auth is True"
            )

        # Check MCP requirements
        if self.enable_mcp and not self.mcp_config_path:
            raise ValueError("mcp_config_path required when enable_mcp is True")

        return self

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return not (self.debug or self.development_mode)

    @property
    def database_path(self) -> Optional[Path]:
        """Extract path from SQLite database URL."""
        if self.database_url.startswith("sqlite:///"):
            db_path = self.database_url.replace("sqlite:///", "")
            return Path(db_path).resolve()
        return None

    @property
    def telegram_token_str(self) -> str:
        """Get Telegram token as string."""
        return self.telegram_bot_token.get_secret_value()

    @property
    def auth_secret_str(self) -> Optional[str]:
        """Get auth token secret as string."""
        if self.auth_token_secret:
            return self.auth_token_secret.get_secret_value()
        return None

    @property
    def anthropic_api_key_str(self) -> Optional[str]:
        """Get Anthropic API key as string."""
        return (
            self.anthropic_api_key.get_secret_value()
            if self.anthropic_api_key
            else None
        )
