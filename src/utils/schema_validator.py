# src/utils/schema_validator.py

import yaml
import pandas as pd
from typing import Dict, Any, List
from pathlib import Path


class SchemaValidationError(Exception):
    """Raised when input data does not match expected schema."""


def load_expected_schema(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def validate_schema(df: pd.DataFrame, schema_path: str, logger=None):
    """Validates dataset columns and basic types before analysis."""

    expected = load_expected_schema(schema_path)
    required_cols: List[str] = expected.get("required_columns", [])
    expected_types: Dict[str, str] = expected.get("dtypes", {})

    df_cols = set(df.columns)
    missing = [c for c in required_cols if c not in df_cols]
    extra = [c for c in df_cols if c not in required_cols]

    if missing:
        msg = f"❌ Missing required columns: {missing}"
        if logger:
            logger.error(msg)
        raise SchemaValidationError(msg)

    if logger:
        logger.info(f"All required columns present: {required_cols}")

    # Optional: log extra cols, but do not fail
    if extra and logger:
        logger.warning(f"Extra columns detected (ignored): {extra}")

    # Type validation (light)
    for col, dtype in expected_types.items():
        if col in df.columns:
            if dtype == "datetime":
                try:
                    pd.to_datetime(df[col])
                except Exception:
                    msg = f"❌ Column `{col}` could not be parsed as datetime."
                    if logger:
                        logger.error(msg)
                    raise SchemaValidationError(msg)

            elif dtype == "float":
                if not pd.api.types.is_numeric_dtype(df[col]):
                    msg = f"❌ Column `{col}` expected float but found {df[col].dtype}."
                    if logger:
                        logger.error(msg)
                    raise SchemaValidationError(msg)

            elif dtype == "int":
                if not pd.api.types.is_integer_dtype(df[col]) and not pd.api.types.is_numeric_dtype(df[col]):
                    msg = f"❌ Column `{col}` expected int but found {df[col].dtype}."
                    if logger:
                        logger.error(msg)
                    raise SchemaValidationError(msg)

            elif dtype == "str":
                if not pd.api.types.is_string_dtype(df[col]):
                    msg = f"⚠ Column `{col}` expected str but got {df[col].dtype}. Attempting conversion."
                    if logger:
                        logger.warning(msg)
                    df[col] = df[col].astype(str)

    if logger:
        logger.info("✅ Schema validation passed.")
