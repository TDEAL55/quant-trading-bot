import pandas as pd

from data_validation import validate_price_data


def test_validate_price_data_accepts_clean_data():
    data = pd.DataFrame({"close": [10.0, 11.0, 12.0]}, index=pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]))
    assert validate_price_data(data) is True


def test_validate_price_data_rejects_empty_data():
    data = pd.DataFrame({"close": []})
    try:
        validate_price_data(data)
    except ValueError as exc:
        assert "empty" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError for empty data")
