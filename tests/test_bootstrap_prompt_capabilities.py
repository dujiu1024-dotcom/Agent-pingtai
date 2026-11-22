from agent_platform.api.bootstrap import resolve_bootstrap_capabilities


def test_prompt_capability_requires_authenticated_configured_registry() -> None:
    assert "prompt_management" not in resolve_bootstrap_capabilities(
        ["prompt_management", "other"],
        auth_enabled=False,
        prompt_registry_configured=True,
        prompt_evaluation_gate_configured=True,
    )
    assert "prompt_management" not in resolve_bootstrap_capabilities(
        ["prompt_management", "other"],
        auth_enabled=True,
        prompt_registry_configured=False,
        prompt_evaluation_gate_configured=False,
    )


def test_prompt_capabilities_follow_actual_registry_and_gate_wiring() -> None:
    capabilities = resolve_bootstrap_capabilities(
        ["other", "prompt_management", "prompt_evaluation_gate"],
        auth_enabled=True,
        prompt_registry_configured=True,
        prompt_evaluation_gate_configured=False,
    )
    assert capabilities == ("other", "prompt_management")

    with_gate = resolve_bootstrap_capabilities(
        ["other"],
        auth_enabled=True,
        prompt_registry_configured=True,
        prompt_evaluation_gate_configured=True,
    )
    assert with_gate == ("other", "prompt_evaluation_gate", "prompt_management")
