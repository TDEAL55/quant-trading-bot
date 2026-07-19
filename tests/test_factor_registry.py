from factor_registry import FactorDefinition, FactorRegistry


def test_register_valid_factor():
    registry = FactorRegistry()
    registry.register(
        FactorDefinition(
            factor_id="x",
            name="X",
            description="desc",
            category="custom",
            version="v1",
            direction="higher_is_better",
            calculation_source="test",
            lookback_period=None,
            minimum_history_required=1,
        )
    )
    assert registry.get("x", "v1").name == "X"


def test_reject_duplicate_factor_id_version():
    registry = FactorRegistry()
    factor = FactorDefinition(
        factor_id="x",
        name="X",
        description="desc",
        category="custom",
        version="v1",
        direction="higher_is_better",
        calculation_source="test",
        lookback_period=None,
        minimum_history_required=1,
    )
    registry.register(factor)
    try:
        registry.register(factor)
        assert False
    except ValueError:
        assert True


def test_reject_invalid_direction():
    registry = FactorRegistry()
    try:
        registry.register(
            FactorDefinition(
                factor_id="x",
                name="X",
                description="desc",
                category="custom",
                version="v1",
                direction="bad",
                calculation_source="test",
                lookback_period=None,
                minimum_history_required=1,
            )
        )
        assert False
    except ValueError:
        assert True


def test_filter_by_category():
    registry = FactorRegistry()
    registry.register(
        FactorDefinition(
            factor_id="a",
            name="A",
            description="desc",
            category="momentum",
            version="v1",
            direction="higher_is_better",
            calculation_source="test",
            lookback_period=None,
            minimum_history_required=1,
        )
    )
    registry.register(
        FactorDefinition(
            factor_id="b",
            name="B",
            description="desc",
            category="trend",
            version="v1",
            direction="higher_is_better",
            calculation_source="test",
            lookback_period=None,
            minimum_history_required=1,
        )
    )
    filtered = registry.list_factors(category="momentum")
    assert [row.factor_id for row in filtered] == ["a"]
