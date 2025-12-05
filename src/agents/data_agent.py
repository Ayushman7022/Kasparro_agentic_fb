import pandas as pd
from typing import Dict, Any, List
import os

from src.utils.schemas import REQUIRED_DATASET_COLUMNS


class DataAgent:
    def __init__(self, data_path: str):
        self.data_path = data_path
        self.df = None

    def _validate_schema(self, df: pd.DataFrame):
        """Validate that required columns are present."""
        missing = [col for col in REQUIRED_DATASET_COLUMNS if col not in df.columns]

        if missing:
            raise ValueError(
                f"❌ Dataset is missing required columns: {missing}. "
                f"Expected: {REQUIRED_DATASET_COLUMNS}"
            )

    def load(self):
        if self.df is None:
            df = pd.read_csv(self.data_path, parse_dates=["date"])
            self._validate_schema(df)
            self.df = df
        return self.df

    def summary(self) -> Dict[str, Any]:
        df = self.load()
        summary = {}

        try:
            summary["n_rows"] = len(df)
            summary["date_min"] = str(df["date"].min()) if "date" in df else None
            summary["date_max"] = str(df["date"].max()) if "date" in df else None

            if "campaign_name" in df:
                summary["campaign_count"] = int(df["campaign_name"].nunique())
                if "spend" in df:
                    summary["top_campaigns_by_spend"] = (
                        df.groupby("campaign_name")["spend"].sum()
                        .sort_values(ascending=False).head(5).to_dict()
                    )
                else:
                    summary["top_campaigns_by_spend"] = {}
            else:
                summary["campaign_count"] = 0
                summary["top_campaigns_by_spend"] = {}

        except Exception as e:
            return {"error": f"summary_failed: {e}", "fallback_used": True}

        return summary

    def get_time_series(self, campaign: str = None, metric: str = "roas", freq: str = "D"):
        df = self.load()

        if metric not in df.columns:
            return pd.DataFrame(), f"metric_missing:{metric}"

        try:
            if campaign and "campaign_name" in df:
                df = df[df["campaign_name"] == campaign]

            ts = (
                df.set_index("date")
                .resample(freq)
                .agg({
                    metric: "mean",
                    "spend": "sum" if "spend" in df else "mean",
                    "impressions": "sum" if "impressions" in df else "mean",
                    "clicks": "sum" if "clicks" in df else "mean",
                })
                .reset_index()
            )

            return ts, None

        except Exception as e:
            return pd.DataFrame(), f"error_resampling:{e}"

    def get_creatives_sample(self, n=20):
        df = self.load()
        cols = ["campaign_name", "adset_name", "creative_type", "creative_message", "ctr"]
        return df[cols].dropna().head(n).to_dict(orient="records")
