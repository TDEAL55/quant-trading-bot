import pandas as pd

from logger_setup import logger


def validate_price_data(data):
    """Validate downloaded price data and raise an error if it is not usable."""
    if data is None:
        logger.error("Validation failed: data is None")
        raise ValueError("Data is None")

    if isinstance(data, pd.DataFrame):
        if data.empty:
            logger.error("Validation failed: dataset is empty")
            raise ValueError("Dataset is empty")

        if data.index.has_duplicates:
            logger.error("Validation failed: duplicate dates found")
            raise ValueError("Duplicate dates found")

        if "close" not in data.columns:
            logger.error("Validation failed: missing close column")
            raise ValueError("Missing close column")

        if data["close"].isna().any():
            logger.error("Validation failed: missing prices detected")
            raise ValueError("Missing prices detected")

        if data["close"].dtype.kind not in "biufc":
            logger.error("Validation failed: incorrect data type for close prices")
            raise ValueError("Incorrect data type for close prices")

        if (data["close"] <= 0).any():
            logger.error("Validation failed: unrealistic price values detected")
            raise ValueError("Unrealistic price values detected")

        return True

    logger.error("Validation failed: data is not a DataFrame")
    raise TypeError("Data must be a pandas DataFrame")
