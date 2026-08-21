from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)


def test_runtime_route_contains_no_secret_material():
    route = RuntimeRoute(
        name="codex_api",
        runtime_kind=RuntimeKind.CODEX_CLI,
        credential_mode=CredentialMode.SERVICE_API,
        model="gpt-5.5",
    )
    assert route.name == "codex_api"
    assert "key" not in route.model_dump()


def test_unclassified_failure_is_fail_closed():
    failure = RuntimeFailure(
        failure_class=RuntimeFailureClass.UNCLASSIFIED,
        code="runtime_unclassified",
        detail="safe detail",
    )
    assert failure.retryable_on_same_route is False
    assert failure.failover_permitted is False
    assert failure.route_pause_required is False
