"""Tests for repository implementations."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.storage.database import DatabaseManager
from src.storage.models import (
    AuditLogModel,
    MessageModel,
    SessionEventModel,
    SessionModel,
    ToolUsageModel,
    UserModel,
)
from src.storage.repositories import (
    ApprovalRequestRepository,
    AnalyticsRepository,
    AuditLogRepository,
    MessageRepository,
    SessionEventRepository,
    SessionRepository,
    ToolUsageRepository,
    UserRepository,
)


@pytest.fixture
async def db_manager():
    """Create test database manager."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        manager = DatabaseManager(f"sqlite:///{db_path}")
        await manager.initialize()
        yield manager
        await manager.close()


@pytest.fixture
async def user_repo(db_manager):
    """Create user repository."""
    return UserRepository(db_manager)


@pytest.fixture
async def session_repo(db_manager):
    """Create session repository."""
    return SessionRepository(db_manager)


@pytest.fixture
async def message_repo(db_manager):
    """Create message repository."""
    return MessageRepository(db_manager)


@pytest.fixture
async def tool_repo(db_manager):
    """Create tool usage repository."""
    return ToolUsageRepository(db_manager)


@pytest.fixture
async def audit_repo(db_manager):
    """Create audit log repository."""
    return AuditLogRepository(db_manager)


@pytest.fixture
async def approval_repo(db_manager):
    """Create approval request repository."""
    return ApprovalRequestRepository(db_manager)


@pytest.fixture
async def session_event_repo(db_manager):
    """Create session event repository."""
    return SessionEventRepository(db_manager)


@pytest.fixture
async def analytics_repo(db_manager):
    """Create analytics repository."""
    return AnalyticsRepository(db_manager)


class TestUserRepository:
    """Test user repository."""

    async def test_create_and_get_user(self, user_repo):
        """Test creating and retrieving user."""
        user = UserModel(
            user_id=12345,
            telegram_username="testuser",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=True,
        )

        # Create user
        created_user = await user_repo.create_user(user)
        assert created_user.user_id == 12345

        # Get user
        retrieved_user = await user_repo.get_user(12345)
        assert retrieved_user is not None
        assert retrieved_user.user_id == 12345
        assert retrieved_user.telegram_username == "testuser"
        assert retrieved_user.is_allowed == 1  # SQLite stores boolean as integer

    async def test_update_user(self, user_repo):
        """Test updating user."""
        user = UserModel(
            user_id=12346,
            telegram_username="testuser2",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=False,
            total_cost=10.5,
            message_count=5,
        )

        await user_repo.create_user(user)

        # Update user
        user.total_cost = 20.0
        user.message_count = 10
        await user_repo.update_user(user)

        # Verify update
        updated_user = await user_repo.get_user(12346)
        assert updated_user.total_cost == 20.0
        assert updated_user.message_count == 10

    async def test_get_allowed_users(self, user_repo):
        """Test getting allowed users."""
        # Create allowed user
        allowed_user = UserModel(
            user_id=12347,
            telegram_username="allowed",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=True,
        )
        await user_repo.create_user(allowed_user)

        # Create disallowed user
        disallowed_user = UserModel(
            user_id=12348,
            telegram_username="disallowed",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=False,
        )
        await user_repo.create_user(disallowed_user)

        # Get allowed users
        allowed_users = await user_repo.get_allowed_users()
        assert 12347 in allowed_users
        assert 12348 not in allowed_users


class TestSessionRepository:
    """Test session repository."""

    async def test_create_and_get_session(self, session_repo, user_repo):
        """Test creating and retrieving session."""
        # Create user first
        user = UserModel(
            user_id=12349,
            telegram_username="sessionuser",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=True,
        )
        await user_repo.create_user(user)

        # Create session
        session = SessionModel(
            session_id="test-session-123",
            user_id=12349,
            project_path="/test/project",
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
            total_cost=5.0,
            total_turns=3,
            message_count=2,
        )

        created_session = await session_repo.create_session(session)
        assert created_session.session_id == "test-session-123"

        # Get session
        retrieved_session = await session_repo.get_session("test-session-123")
        assert retrieved_session is not None
        assert retrieved_session.user_id == 12349
        assert retrieved_session.project_path == "/test/project"

    async def test_get_user_sessions(self, session_repo, user_repo):
        """Test getting user sessions."""
        # Create user
        user = UserModel(
            user_id=12350,
            telegram_username="multisessionuser",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=True,
        )
        await user_repo.create_user(user)

        # Create multiple sessions
        for i in range(3):
            session = SessionModel(
                session_id=f"test-session-{i}",
                user_id=12350,
                project_path=f"/test/project{i}",
                created_at=datetime.utcnow(),
                last_used=datetime.utcnow(),
            )
            await session_repo.create_session(session)

        # Get user sessions
        sessions = await session_repo.get_user_sessions(12350)
        assert len(sessions) == 3
        assert all(s.user_id == 12350 for s in sessions)

    async def test_cleanup_old_sessions(self, session_repo, user_repo):
        """Test cleaning up old sessions."""
        # Create user
        user = UserModel(
            user_id=12351,
            telegram_username="cleanupuser",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=True,
        )
        await user_repo.create_user(user)

        # Create old session
        old_session = SessionModel(
            session_id="old-session",
            user_id=12351,
            project_path="/test/old",
            created_at=datetime.utcnow() - timedelta(days=35),
            last_used=datetime.utcnow() - timedelta(days=35),
        )
        await session_repo.create_session(old_session)

        # Create recent session
        recent_session = SessionModel(
            session_id="recent-session",
            user_id=12351,
            project_path="/test/recent",
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        )
        await session_repo.create_session(recent_session)

        # Cleanup old sessions
        cleaned = await session_repo.cleanup_old_sessions(days=30)
        assert cleaned == 1

        # Check that only recent session is active
        active_sessions = await session_repo.get_user_sessions(12351, active_only=True)
        assert len(active_sessions) == 1
        assert active_sessions[0].session_id == "recent-session"


class TestMessageRepository:
    """Test message repository."""

    async def test_save_and_get_messages(self, message_repo, session_repo, user_repo):
        """Test saving and retrieving messages."""
        # Setup user and session
        user = UserModel(
            user_id=12352,
            telegram_username="messageuser",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=True,
        )
        await user_repo.create_user(user)

        session = SessionModel(
            session_id="message-session",
            user_id=12352,
            project_path="/test/messages",
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        )
        await session_repo.create_session(session)

        # Save message
        message = MessageModel(
            session_id="message-session",
            user_id=12352,
            timestamp=datetime.utcnow(),
            prompt="Test prompt",
            response="Test response",
            cost=0.05,
            duration_ms=1500,
        )

        message_id = await message_repo.save_message(message)
        assert message_id is not None

        # Get session messages
        messages = await message_repo.get_session_messages("message-session")
        assert len(messages) == 1
        assert messages[0].prompt == "Test prompt"
        assert messages[0].response == "Test response"


class TestToolUsageRepository:
    """Test tool usage repository."""

    async def test_save_and_get_tool_usage(self, tool_repo, session_repo, user_repo):
        """Test saving and retrieving tool usage."""
        # Setup user and session
        user = UserModel(
            user_id=12353,
            telegram_username="tooluser",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=True,
        )
        await user_repo.create_user(user)

        session = SessionModel(
            session_id="tool-session",
            user_id=12353,
            project_path="/test/tools",
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        )
        await session_repo.create_session(session)

        # Save tool usage
        tool_usage = ToolUsageModel(
            session_id="tool-session",
            tool_name="Read",
            tool_input={"file_path": "/test/file.py"},
            timestamp=datetime.utcnow(),
            success=True,
        )

        usage_id = await tool_repo.save_tool_usage(tool_usage)
        assert usage_id is not None

        # Get session tool usage
        usage_records = await tool_repo.get_session_tool_usage("tool-session")
        assert len(usage_records) == 1
        assert usage_records[0].tool_name == "Read"
        assert usage_records[0].tool_input["file_path"] == "/test/file.py"

    async def test_get_tool_stats(self, tool_repo, session_repo, user_repo):
        """Test getting tool statistics."""
        # Setup user and session
        user = UserModel(
            user_id=12354,
            telegram_username="statsuser",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=True,
        )
        await user_repo.create_user(user)

        session = SessionModel(
            session_id="stats-session",
            user_id=12354,
            project_path="/test/stats",
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        )
        await session_repo.create_session(session)

        # Create multiple tool usages
        tools = ["Read", "Write", "Read", "Edit", "Read"]
        for tool in tools:
            tool_usage = ToolUsageModel(
                session_id="stats-session",
                tool_name=tool,
                timestamp=datetime.utcnow(),
                success=True,
            )
            await tool_repo.save_tool_usage(tool_usage)

        # Get tool stats
        stats = await tool_repo.get_tool_stats()

        # Find Read tool stats
        read_stats = next(s for s in stats if s["tool_name"] == "Read")
        assert read_stats["usage_count"] == 3
        assert read_stats["success_count"] == 3
        assert read_stats["error_count"] == 0


class TestApprovalRequestRepository:
    """Test approval request repository."""

    async def test_create_and_resolve_request(self, approval_repo, db_manager):
        """Pending request should transition once and remain idempotent."""
        request_id = "req-1234"
        created_at = datetime.utcnow()

        await approval_repo.create_request(
            request_id=request_id,
            user_id=12356,
            session_id="session-a",
            tool_name="Bash",
            tool_input={"command": "pytest"},
            expires_at=created_at + timedelta(minutes=2),
        )

        resolved = await approval_repo.resolve_request(
            request_id=request_id,
            status="approved",
            decision="allow",
            resolved_at=created_at + timedelta(seconds=10),
        )
        assert resolved is True

        resolved_again = await approval_repo.resolve_request(
            request_id=request_id,
            status="denied",
            decision="deny",
            resolved_at=created_at + timedelta(seconds=20),
        )
        assert resolved_again is False

        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT status, decision, tool_name
                FROM approval_requests
                WHERE request_id = ?
                """,
                (request_id,),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row["status"] == "approved"
        assert row["decision"] == "allow"
        assert row["tool_name"] == "Bash"

    async def test_expire_all_pending(self, approval_repo, db_manager):
        """Startup recovery should expire only pending rows."""
        now = datetime.utcnow()
        await approval_repo.create_request(
            request_id="req-pending",
            user_id=12357,
            session_id="session-b",
            tool_name="Write",
            tool_input={"file_path": "a.py"},
            expires_at=now + timedelta(minutes=2),
        )
        await approval_repo.create_request(
            request_id="req-approved",
            user_id=12357,
            session_id="session-b",
            tool_name="Read",
            tool_input={"file_path": "a.py"},
            expires_at=now + timedelta(minutes=2),
        )
        await approval_repo.resolve_request(
            request_id="req-approved",
            status="approved",
            decision="allow",
            resolved_at=now + timedelta(seconds=1),
        )

        expired_count = await approval_repo.expire_all_pending(resolved_at=now)
        assert expired_count == 1

        async with db_manager.get_connection() as conn:
            pending_cursor = await conn.execute(
                "SELECT status FROM approval_requests WHERE request_id = ?",
                ("req-pending",),
            )
            pending_row = await pending_cursor.fetchone()
            approved_cursor = await conn.execute(
                "SELECT status FROM approval_requests WHERE request_id = ?",
                ("req-approved",),
            )
            approved_row = await approved_cursor.fetchone()

        assert pending_row["status"] == "expired"
        assert approved_row["status"] == "approved"


class TestSessionEventRepository:
    """Test session event repository."""

    async def test_save_and_query_session_events(
        self,
        session_event_repo,
        session_repo,
        user_repo,
    ):
        """Session events should be persisted and queryable by type."""
        now = datetime.utcnow()
        await user_repo.create_user(
            UserModel(
                user_id=12358,
                telegram_username="event_user",
                first_seen=now,
                last_active=now,
                is_allowed=True,
            )
        )
        await session_repo.create_session(
            SessionModel(
                session_id="event-session",
                user_id=12358,
                project_path="/test/events",
                created_at=now,
                last_used=now,
            )
        )

        await session_event_repo.save_event(
            SessionEventModel(
                id=None,
                session_id="event-session",
                event_type="command_exec",
                event_data={"prompt": "run tests"},
                created_at=now,
            )
        )
        await session_event_repo.save_events(
            [
                SessionEventModel(
                    id=None,
                    session_id="event-session",
                    event_type="tool_call",
                    event_data={"tool_name": "Bash"},
                    created_at=now + timedelta(seconds=1),
                ),
                SessionEventModel(
                    id=None,
                    session_id="event-session",
                    event_type="tool_result",
                    event_data={"tool_name": "Bash", "success": True},
                    created_at=now + timedelta(seconds=2),
                ),
            ]
        )

        all_events = await session_event_repo.get_session_events(
            "event-session",
            limit=10,
        )
        assert len(all_events) == 3
        assert all_events[0].event_type == "tool_result"
        assert all_events[-1].event_type == "command_exec"

        tool_events = await session_event_repo.get_session_events(
            "event-session",
            event_types=["tool_call", "tool_result"],
            limit=10,
        )
        assert len(tool_events) == 2
        assert {e.event_type for e in tool_events} == {"tool_call", "tool_result"}


class TestAuditLogRepository:
    """Test audit log repository filters."""

    async def test_log_event_auto_creates_missing_user(self, audit_repo, user_repo):
        """Audit logging should not fail when user row does not exist yet."""
        now = datetime.utcnow()
        missing_user_id = 9901

        await audit_repo.log_event(
            AuditLogModel(
                id=None,
                user_id=missing_user_id,
                event_type="security_violation",
                event_data={"violation_type": "system_error"},
                success=False,
                timestamp=now,
                ip_address=None,
            )
        )

        created_user = await user_repo.get_user(missing_user_id)
        assert created_user is not None
        assert created_user.user_id == missing_user_id
        assert created_user.is_allowed == 0  # SQLite stores boolean as integer

        events = await audit_repo.get_user_audit_log(missing_user_id, limit=10)
        assert len(events) == 1
        assert events[0].event_type == "security_violation"

    async def test_get_events_with_filters(self, audit_repo, user_repo):
        """Repository should support combined user/type/time filtering."""
        now = datetime.utcnow()
        await user_repo.create_user(
            UserModel(
                user_id=9001,
                telegram_username="audit_u1",
                first_seen=now,
                last_active=now,
                is_allowed=True,
            )
        )
        await user_repo.create_user(
            UserModel(
                user_id=9002,
                telegram_username="audit_u2",
                first_seen=now,
                last_active=now,
                is_allowed=True,
            )
        )

        await audit_repo.log_event(
            AuditLogModel(
                id=None,
                user_id=9001,
                event_type="auth_attempt",
                event_data={"method": "token"},
                success=False,
                timestamp=now - timedelta(hours=2),
                ip_address=None,
            )
        )
        await audit_repo.log_event(
            AuditLogModel(
                id=None,
                user_id=9001,
                event_type="command",
                event_data={"command": "ls"},
                success=True,
                timestamp=now,
                ip_address=None,
            )
        )
        await audit_repo.log_event(
            AuditLogModel(
                id=None,
                user_id=9002,
                event_type="command",
                event_data={"command": "pwd"},
                success=True,
                timestamp=now,
                ip_address=None,
            )
        )

        events = await audit_repo.get_events(
            user_id=9001,
            event_type="command",
            start_time=now - timedelta(minutes=30),
            limit=20,
        )
        assert len(events) == 1
        assert events[0].user_id == 9001
        assert events[0].event_type == "command"
        assert events[0].event_data["command"] == "ls"

    async def test_get_security_violations(self, audit_repo, user_repo):
        """Security-violation query should filter by event_type."""
        now = datetime.utcnow()
        await user_repo.create_user(
            UserModel(
                user_id=9003,
                telegram_username="audit_u3",
                first_seen=now,
                last_active=now,
                is_allowed=True,
            )
        )

        await audit_repo.log_event(
            AuditLogModel(
                id=None,
                user_id=9003,
                event_type="command",
                event_data={"command": "echo"},
                success=True,
                timestamp=now,
                ip_address=None,
            )
        )
        await audit_repo.log_event(
            AuditLogModel(
                id=None,
                user_id=9003,
                event_type="security_violation",
                event_data={"violation_type": "injection"},
                success=False,
                timestamp=now,
                ip_address=None,
            )
        )

        violations = await audit_repo.get_security_violations(user_id=9003, limit=10)
        assert len(violations) == 1
        assert violations[0].event_type == "security_violation"
        assert violations[0].event_data["violation_type"] == "injection"


class TestAnalyticsRepository:
    """Test analytics repository."""

    async def test_get_system_stats(
        self, analytics_repo, message_repo, session_repo, user_repo
    ):
        """Test getting system statistics."""
        # Setup test data
        user = UserModel(
            user_id=12355,
            telegram_username="analyticsuser",
            first_seen=datetime.utcnow(),
            last_active=datetime.utcnow(),
            is_allowed=True,
        )
        await user_repo.create_user(user)

        session = SessionModel(
            session_id="analytics-session",
            user_id=12355,
            project_path="/test/analytics",
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        )
        await session_repo.create_session(session)

        # Create messages
        for i in range(3):
            message = MessageModel(
                session_id="analytics-session",
                user_id=12355,
                timestamp=datetime.utcnow(),
                prompt=f"Test prompt {i}",
                response=f"Test response {i}",
                cost=0.1,
            )
            await message_repo.save_message(message)

        # Get system stats
        stats = await analytics_repo.get_system_stats()

        assert stats["overall"]["total_users"] >= 1
        assert stats["overall"]["total_sessions"] >= 1
        assert stats["overall"]["total_messages"] >= 3
        assert stats["overall"]["total_cost"] >= 0.3
