"""Telegram bot authentication middleware."""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict

import structlog
from telegram.ext import ApplicationHandlerStop

from ..utils.telegram_send import send_message_resilient

logger = structlog.get_logger()
BEIJING_TZ = timezone(timedelta(hours=8), name="UTC+8")


async def _reply_event_message_resilient(event: Any, text: str) -> Any:
    """Reply via message first, then fallback to resilient send helper."""
    message = getattr(event, "effective_message", None)
    if message is None:
        return None

    try:
        return await message.reply_text(text)
    except Exception:
        get_bot = getattr(message, "get_bot", None)
        bot = None
        if callable(get_bot):
            try:
                bot = get_bot()
            except Exception:
                bot = None

        chat_obj = getattr(message, "chat", None)
        chat_id = getattr(message, "chat_id", None)
        if not isinstance(chat_id, int):
            chat_id = getattr(chat_obj, "id", None)
        if bot is None or not isinstance(chat_id, int):
            raise

        return await send_message_resilient(
            bot=bot,
            chat_id=chat_id,
            text=text,
            message_thread_id=getattr(message, "message_thread_id", None),
            chat_type=getattr(chat_obj, "type", None),
            reply_to_message_id=getattr(message, "message_id", None),
        )


async def auth_middleware(handler: Callable, event: Any, data: Dict[str, Any]) -> Any:
    """Check authentication before processing messages.

    This middleware:
    1. Checks if user is authenticated
    2. Attempts authentication if not authenticated
    3. Updates session activity
    4. Logs authentication events
    """
    # Extract user information
    user_id = event.effective_user.id if event.effective_user else None
    username = (
        getattr(event.effective_user, "username", None)
        if event.effective_user
        else None
    )

    if not user_id:
        logger.warning("No user information in update")
        raise ApplicationHandlerStop

    # Get dependencies from context
    auth_manager = data.get("auth_manager")
    audit_logger = data.get("audit_logger")

    if not auth_manager:
        logger.error("Authentication manager not available in middleware context")
        if event.effective_message:
            await _reply_event_message_resilient(
                event, "🔒 Authentication system unavailable. Please try again later."
            )
        raise ApplicationHandlerStop

    # Check if user is already authenticated
    if auth_manager.is_authenticated(user_id):
        # Update session activity
        if auth_manager.refresh_session(user_id):
            session = auth_manager.get_session(user_id)
            logger.debug(
                "Session refreshed",
                user_id=user_id,
                username=username,
                auth_provider=session.auth_provider if session else None,
            )

        # Continue to handler
        return await handler(event, data)

    # User not authenticated - attempt authentication
    logger.info(
        "Attempting authentication for user", user_id=user_id, username=username
    )

    # Try to authenticate (providers will check whitelist and tokens)
    authentication_successful = await auth_manager.authenticate_user(user_id)

    # Log authentication attempt
    if audit_logger:
        await audit_logger.log_auth_attempt(
            user_id=user_id,
            success=authentication_successful,
            method="automatic",
            reason="message_received",
        )

    if authentication_successful:
        session = auth_manager.get_session(user_id)
        logger.info(
            "User authenticated successfully",
            user_id=user_id,
            username=username,
            auth_provider=session.auth_provider if session else None,
        )

        # Welcome message for new session
        if event.effective_message:
            session_started_bjt = datetime.now(BEIJING_TZ).strftime("%H:%M:%S")
            await _reply_event_message_resilient(
                event,
                f"🔓 Welcome! You are now authenticated.\n"
                f"Session started at {session_started_bjt} Beijing Time (UTC+8)",
            )

        # Continue to handler
        return await handler(event, data)

    else:
        # Authentication failed
        logger.warning("Authentication failed", user_id=user_id, username=username)

        if event.effective_message:
            await _reply_event_message_resilient(
                event,
                "🔒 **Authentication Required**\n\n"
                "You are not authorized to use this bot.\n"
                "Please contact the administrator for access.\n\n"
                f"Your Telegram ID: `{user_id}`\n"
                "Share this ID with the administrator to request access.",
            )
        raise ApplicationHandlerStop


async def require_auth(handler: Callable, event: Any, data: Dict[str, Any]) -> Any:
    """Decorator-style middleware that requires authentication.

    This is a stricter version that only allows authenticated users.
    """
    user_id = event.effective_user.id if event.effective_user else None
    auth_manager = data.get("auth_manager")

    if not auth_manager or not auth_manager.is_authenticated(user_id):
        if event.effective_message:
            await _reply_event_message_resilient(
                event, "🔒 Authentication required to use this command."
            )
        return

    return await handler(event, data)


async def admin_required(handler: Callable, event: Any, data: Dict[str, Any]) -> Any:
    """Middleware that requires admin privileges.

    Note: This is a placeholder - admin privileges would need to be
    implemented in the authentication system.
    """
    user_id = event.effective_user.id if event.effective_user else None
    auth_manager = data.get("auth_manager")

    if not auth_manager or not auth_manager.is_authenticated(user_id):
        if event.effective_message:
            await _reply_event_message_resilient(event, "🔒 Authentication required.")
        return

    session = auth_manager.get_session(user_id)
    if not session or not session.user_info:
        if event.effective_message:
            await _reply_event_message_resilient(
                event, "🔒 Session information unavailable."
            )
        return

    # Check for admin permissions (placeholder logic)
    permissions = session.user_info.get("permissions", [])
    if "admin" not in permissions:
        if event.effective_message:
            await _reply_event_message_resilient(
                event,
                "🔒 **Admin Access Required**\n\n"
                "This command requires administrator privileges.",
            )
        return

    return await handler(event, data)
