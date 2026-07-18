from strategy_definitions import built_in_strategy_definitions, definition_by_id, strategy_fingerprint, validate_strategy_definitions


def test_built_in_strategy_definitions_contains_baseline():
    definitions = built_in_strategy_definitions()
    assert len(definitions) >= 10
    baseline = definition_by_id(definitions, "baseline_scanner")
    assert baseline.strategy_name
    assert baseline.enabled is True


def test_strategy_fingerprint_is_deterministic():
    first = strategy_fingerprint({"a": 1, "b": [2, 3]})
    second = strategy_fingerprint({"b": [2, 3], "a": 1})
    assert first == second


def test_validate_strategy_definitions_accepts_builtins():
    definitions = built_in_strategy_definitions()
    validate_strategy_definitions(definitions)
