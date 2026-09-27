"""Unit tests for the exception taxonomy (kingdoms-services#10).

The hierarchy is the contract: every failure of the platform is a
KingdomsError, the class is the category, the code rides the logs
and audits, the user_key selects the i18n answer, and the context
carries serializable data only (ADR-0019 rule 3).
"""

from __future__ import annotations

import pytest

from kingdoms.core.exceptions import (
    ConfigurationError,
    ConnectionError,
    DatabaseError,
    DuplicateKeyError,
    ErrorContext,
    ExceptionHandler,
    InvalidWorkflowStepError,
    KingdomsError,
    MessageSendError,
    NotFoundError,
    PermissionError,
    PlatformError,
    PlatformNotSupportedError,
    QueryError,
    RateLimitError,
    UserNotFoundError,
    ValidationError,
    WorkflowAlreadyCompletedError,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowTimeoutError,
)
from kingdoms.core.exceptions.handlers import FALLBACK_USER_KEY


class TestBaseHierarchy:
    def test_every_exception_is_a_kingdoms_error(self) -> None:
        errors = [
            ValidationError("bad", field="age"),
            ConfigurationError("missing", config_key="TOKEN"),
            NotFoundError("Clan", "42"),
            PermissionError("no"),
            RateLimitError(),
            WorkflowError("failed"),
            PlatformError("down"),
            DatabaseError("boom"),
        ]
        for error in errors:
            assert isinstance(error, KingdomsError)
            assert isinstance(error, Exception)

    def test_the_str_carries_the_code(self) -> None:
        error = NotFoundError("Clan", "42")
        assert str(error) == "[NOT_FOUND] Clan not found: 42"

    def test_to_dict_is_serializable_audit_shape(self) -> None:
        error = ValidationError("bad age", field="age", value="abc")
        payload = error.to_dict()
        assert payload["error"] == "VALIDATION_ERROR"
        assert payload["message"] == "bad age"
        assert payload["details"]["field"] == "age"
        assert payload["details"]["value"] == "abc"

    def test_the_context_is_immutable_and_extensible(self) -> None:
        context = ErrorContext().extend(guild_id="1").extend(mod="clans")
        assert context.data == {"guild_id": "1", "mod": "clans"}
        original = ErrorContext().extend(a="1")
        original.extend(a="2")
        assert original.data == {"a": "1"}


class TestCodesAndUserKeys:
    def test_each_family_has_its_code(self) -> None:
        assert ValidationError("x").code == "VALIDATION_ERROR"
        assert ConfigurationError("x").code == "CONFIGURATION_ERROR"
        assert NotFoundError("T", "1").code == "NOT_FOUND"
        assert PermissionError("x").code == "PERMISSION_ERROR"
        assert RateLimitError().code == "RATE_LIMIT_ERROR"
        assert WorkflowError("x").code == "WORKFLOW_ERROR"
        assert PlatformError("x").code == "PLATFORM_ERROR"
        assert DatabaseError("x").code == "DATABASE_ERROR"

    def test_the_user_key_maps_to_the_i18n_surface(self) -> None:
        assert ValidationError("x").user_key == "errors.validation"
        assert NotFoundError("T", "1").user_key == "errors.not_found"
        assert RateLimitError().user_key == "errors.rate_limited"

    def test_an_unknown_code_falls_back_to_the_generic_answer(self) -> None:
        class ExoticError(KingdomsError):
            code = "SOMETHING_ELSE"

        assert ExoticError("boom").user_key == "errors.unexpected"


class TestWorkflowFamily:
    def test_not_found_carries_the_workflow_id(self) -> None:
        error = WorkflowNotFoundError("reg-42")
        assert isinstance(error, WorkflowError)
        assert error.context.data["workflow_id"] == "reg-42"

    def test_invalid_step_names_the_expected_steps(self) -> None:
        error = InvalidWorkflowStepError("reg-1", "fly", ["walk", "run"])
        assert "walk" in error.message
        assert error.context.data["step"] == "fly"

    def test_timeout_carries_the_value(self) -> None:
        error = WorkflowTimeoutError("reg-1", 30)
        assert error.context.data["timeout"] == 30

    def test_already_completed_is_a_workflow_error(self) -> None:
        assert isinstance(WorkflowAlreadyCompletedError("reg-1"), WorkflowError)


class TestPlatformAndDatabaseFamilies:
    def test_platform_not_supported_names_the_feature(self) -> None:
        error = PlatformNotSupportedError("discord", "voice")
        assert error.context.data["platform"] == "discord"
        assert error.context.data["feature"] == "voice"

    def test_message_send_names_the_channel(self) -> None:
        error = MessageSendError("boom", "discord", channel_id="42")
        assert error.context.data["channel_id"] == "42"

    def test_channel_and_user_not_found_are_not_found(self) -> None:
        from kingdoms.core.exceptions import ChannelNotFoundError

        assert ChannelNotFoundError("42", "discord").code == "NOT_FOUND"
        assert UserNotFoundError("7", "discord").code == "NOT_FOUND"

    def test_duplicate_key_names_the_offender(self) -> None:
        error = DuplicateKeyError("clans", "tag", "KNG")
        assert error.context.data["collection"] == "clans"
        assert error.context.data["key"] == "tag"

    def test_connection_and_query_are_database_errors(self) -> None:
        assert isinstance(ConnectionError(), DatabaseError)
        assert isinstance(QueryError("boom", query="find"), DatabaseError)


class TestHandler:
    def test_a_kingdoms_error_resolves_its_user_key(self) -> None:
        assert ExceptionHandler.user_key(ValidationError("x")) == "errors.validation"

    def test_a_foreign_error_falls_back(self) -> None:
        assert ExceptionHandler.user_key(ValueError("boom")) == FALLBACK_USER_KEY

    def test_the_audit_payload_carries_the_taxonomy(self) -> None:
        payload = ExceptionHandler.audit_payload(DuplicateKeyError("clans", "tag", "KNG"))
        assert payload["error"] == "DATABASE_ERROR"

    def test_a_foreign_audit_payload_names_the_type(self) -> None:
        payload = ExceptionHandler.audit_payload(ValueError("boom"))
        assert payload["error"] == "UNEXPECTED"
        assert payload["details"]["type"] == "ValueError"

    def test_log_never_raises(self) -> None:
        ExceptionHandler.log(ValidationError("x"))
        ExceptionHandler.log(ValueError("boom"))
        assert True


class TestMigratedExceptions:
    def test_workflow_not_found_from_the_engine_is_a_kingdoms_error(self) -> None:
        from kingdoms.core.services.workflow import WorkflowNotFoundError as EngineNotFound

        error = EngineNotFound("reg-42")
        assert isinstance(error, KingdomsError)
        assert isinstance(error, WorkflowError)

    def test_state_service_error_is_a_database_error(self) -> None:
        from kingdoms.core.services.state import StateServiceError

        assert isinstance(StateServiceError("redis down"), DatabaseError)

    @pytest.mark.asyncio
    async def test_the_engine_still_raises_the_migrated_types(self) -> None:
        from kingdoms.core.services.workflow import WorkflowEngine, WorkflowNotRegisteredError

        engine = WorkflowEngine(store=_EmptyStore(), state=_NoopState())
        with pytest.raises(WorkflowNotRegisteredError) as excinfo:
            await engine.start_workflow("nope", "1", "2")
        assert isinstance(excinfo.value, KingdomsError)


class _NoopState:
    """A state service stand-in (the start path never reaches it here)."""

    async def get(self, scope: str, key: str) -> object | None:
        return None

    async def set(self, scope: str, key: str, value: object, ttl: int = 0) -> None:
        return None


class _EmptyStore:
    """A minimal workflow store standing in for the real persistence."""

    async def save(self, state: object) -> None:
        return None

    async def load(self, workflow_id: str) -> object | None:
        return None

    async def delete(self, workflow_id: str) -> bool:
        return False

    async def list_by_status(self, status: str) -> list[object]:
        return []
