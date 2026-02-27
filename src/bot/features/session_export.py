"""Session export functionality for exporting chat history in various formats."""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from html import escape

from src.storage.facade import Storage
from src.storage.models import MessageModel, SessionModel
from src.utils.constants import MAX_SESSION_LENGTH


class ExportFormat(Enum):
    """Supported export formats."""

    MARKDOWN = "markdown"
    JSON = "json"
    HTML = "html"


@dataclass
class ExportedSession:
    """Exported session data."""

    format: ExportFormat
    content: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime


class SessionExporter:
    """Handles exporting chat sessions in various formats."""

    def __init__(self, storage: Storage):
        """Initialize exporter with storage dependency.

        Args:
            storage: Storage facade for session data access
        """
        self.storage = storage

    async def export_session(
        self,
        user_id: int,
        session_id: str,
        format: ExportFormat = ExportFormat.MARKDOWN,
    ) -> ExportedSession:
        """Export a session in the specified format.

        Args:
            user_id: User ID
            session_id: Session ID to export
            format: Export format (markdown, json, html)

        Returns:
            ExportedSession with exported content

        Raises:
            ValueError: If session not found or invalid format
        """
        # Get session data
        session = await self.storage.sessions.get_session(session_id)
        if not session or session.user_id != user_id:
            raise ValueError(f"Session {session_id} not found")

        # Get session messages
        messages = await self.storage.messages.get_session_messages(
            session_id, limit=MAX_SESSION_LENGTH
        )
        ordered_messages = sorted(messages, key=lambda msg: msg.timestamp)

        # Export based on format
        if format == ExportFormat.MARKDOWN:
            content = self._export_markdown(session, ordered_messages)
            mime_type = "text/markdown"
            extension = "md"
        elif format == ExportFormat.JSON:
            content = self._export_json(session, ordered_messages)
            mime_type = "application/json"
            extension = "json"
        elif format == ExportFormat.HTML:
            content = self._export_html(session, ordered_messages)
            mime_type = "text/html"
            extension = "html"
        else:
            raise ValueError(f"Unsupported export format: {format}")

        # Create filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"session_{session_id[:8]}_{timestamp}.{extension}"

        return ExportedSession(
            format=format,
            content=content,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content.encode("utf-8")),
            created_at=datetime.utcnow(),
        )

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        """Format datetime for exported content."""
        return value.strftime("%Y-%m-%d %H:%M:%S")

    def _export_markdown(
        self, session: SessionModel, messages: list[MessageModel]
    ) -> str:
        """Export session as Markdown.

        Args:
            session: Session metadata model
            messages: Session message models

        Returns:
            Markdown formatted content
        """
        lines: list[str] = []

        # Header
        lines.append("# CLITG Session Export")
        lines.append(f"\n**Session ID:** `{session.session_id}`")
        lines.append(f"**User ID:** {session.user_id}")
        lines.append(f"**Project:** `{session.project_path}`")
        lines.append(f"**Created:** {self._format_datetime(session.created_at)}")
        lines.append(f"**Last Used:** {self._format_datetime(session.last_used)}")
        lines.append(f"**Message Count:** {len(messages)}")
        lines.append("\n---\n")

        # Messages
        for msg in messages:
            timestamp = self._format_datetime(msg.timestamp)
            lines.append(f"## Message {msg.message_id or '-'} - {timestamp}")
            lines.append("")
            lines.append("### You")
            lines.append("")
            lines.append(msg.prompt or "_(empty)_")
            lines.append("")
            lines.append("### Assistant")
            lines.append("")
            lines.append(msg.response or "_(no response)_")
            if msg.error:
                lines.append("")
                lines.append(f"**Error:** `{msg.error}`")
            lines.append("\n---\n")

        return "\n".join(lines)

    def _export_json(self, session: SessionModel, messages: list[MessageModel]) -> str:
        """Export session as JSON.

        Args:
            session: Session metadata model
            messages: Session message models

        Returns:
            JSON formatted content
        """
        export_data = {
            "session": {
                "id": session.session_id,
                "user_id": session.user_id,
                "project_path": session.project_path,
                "created_at": session.created_at.isoformat(),
                "last_used": session.last_used.isoformat(),
                "message_count": len(messages),
                "total_cost": session.total_cost,
                "total_turns": session.total_turns,
            },
            "messages": [
                {
                    "id": msg.message_id,
                    "timestamp": msg.timestamp.isoformat(),
                    "prompt": msg.prompt,
                    "response": msg.response,
                    "cost": msg.cost,
                    "duration_ms": msg.duration_ms,
                    "error": msg.error,
                }
                for msg in messages
            ],
        }

        return json.dumps(export_data, indent=2, ensure_ascii=False)

    def _export_html(self, session: SessionModel, messages: list[MessageModel]) -> str:
        """Export session as HTML.

        Args:
            session: Session metadata model
            messages: Session message models

        Returns:
            HTML formatted content
        """
        rendered_messages: list[str] = []
        for msg in messages:
            timestamp = self._format_datetime(msg.timestamp)
            error_html = (
                f'<p class="error"><strong>Error:</strong> {escape(msg.error)}</p>'
                if msg.error
                else ""
            )
            rendered_messages.append(
                '<section class="message">'
                f'<h3>Message {msg.message_id or "-"}</h3>'
                f'<p class="timestamp">{escape(timestamp)}</p>'
                "<h4>You</h4>"
                f"<pre>{escape(msg.prompt or '')}</pre>"
                "<h4>Assistant</h4>"
                f"<pre>{escape(msg.response or '')}</pre>"
                f"{error_html}"
                "</section>"
            )
        messages_html = "\n".join(rendered_messages) or "<p>No messages found.</p>"

        # HTML template
        template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CLITG Session - {escape(session.session_id[:8])}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
                Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        h3 {{
            color: #34495e;
            margin-top: 20px;
        }}
        code {{
            background-color: #f8f8f8;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }}
        pre {{
            background-color: #f8f8f8;
            padding: 15px;
            border-radius: 5px;
            overflow-x: auto;
            border: 1px solid #e1e4e8;
        }}
        .metadata {{
            background-color: #f0f7ff;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}
        .message {{
            margin: 20px 0;
            padding: 15px;
            border-left: 4px solid #3498db;
            background-color: #f9f9f9;
        }}
        .message.claude {{
            border-left-color: #2ecc71;
        }}
        .timestamp {{
            color: #7f8c8d;
            font-size: 0.9em;
        }}
        .error {{
            color: #b00020;
        }}
        hr {{
            border: none;
            border-top: 1px solid #e1e4e8;
            margin: 30px 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>CLITG Session Export</h1>
        <div class="metadata">
            <p><strong>Session ID:</strong>
                <code>{escape(session.session_id)}</code></p>
            <p><strong>User ID:</strong> {session.user_id}</p>
            <p><strong>Project:</strong>
                <code>{escape(session.project_path)}</code></p>
            <p><strong>Created:</strong>
                {escape(self._format_datetime(session.created_at))}</p>
            <p><strong>Last Used:</strong>
                {escape(self._format_datetime(session.last_used))}</p>
            <p><strong>Message Count:</strong> {len(messages)}</p>
        </div>
        {messages_html}
    </div>
</body>
</html>"""

        return template
