# src/agents/data_agent.py

import os
import pandas as pd
from typing import Dict, Any, List, Optional


class DataAgent:
    """
    Handles loading the FB ads dataset and exposing helper utilities
    for summary, time series, creatives sampling, etc.
    """

    def __init__(self, data_path: str):

        self.data_path = data_path
        self.df: Optional[pd.DataFrame] = None
        self.logger = None   # will be attached by orchestrator

    # ---------------------------------------------------------
    def _log(self, msg: str, level: str = "info"):
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    # ---------------------------------------------------------
    def load(self) -> pd.DataFrame:
        """Loads CSV only once and caches it."""
        if self.df is not None:
            return self.df

        self._log(f"DataAgent: loading dataset from {self.data_path}")

        if not os.path.exists(self.data_path):
            raise FileNotFoundError(f"Dataset not found at {self.data_path}")

        try:
            df = pd.read_csv(self.data_path)

            # Ensure `date` parses properly
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")

            self.df = df

            self._log(f"DataAgent: loaded {len(df)} rows and {len(df.columns)} columns")

            return df

        except Exception as e:
            self._log(f"DataAgent: ERROR loading dataset: {e}", level="error")
            raise

    # ---------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        """Returns lightweight summary for planner/insight agents."""
        df = self.load()

        required_cols = ["date", "campaign_name", "spend"]

        for col in required_cols:
            if col not in df.columns:
                self._log(f"DataAgent: missing required column '{col}' in dataset.", level="warning")

        summary = {
            "n_rows": len(df),
            "date_min": str(df["date"].min()) if "date" in df else None,
            "date_max": str(df["date"].max()) if "date" in df else None,
            "campaign_count": int(df["campaign_name"].nunique()) if "campaign_name" in df else None,
            "top_campaigns_by_spend": (
                df.groupby("campaign_name")["spend"].sum().sort_values(ascending=False).head(5).to_dict()
                if "campaign_name" in df and "spend" in df
                else {}
            )
        }

        self._log(f"DataAgent: summary generated → {summary['n_rows']} rows, {summary['campaign_count']} campaigns")
        return summary

    # ---------------------------------------------------------
    def get_time_series(self, campaign: str = None, metric: str = "roas", freq: str = "D"):
        """
        Returns aggregated time-series:
        - mean(metric) per day
        - sum(spend)
        - sum(impressions)
        - sum(clicks)
        """

        df = self.load()

        if "date" not in df.columns:
            raise ValueError("Dataset missing 'date' column.")

        # Filter campaign
        if campaign:
            df = df[df["campaign_name"] == campaign]
            self._log(f"DataAgent: time-series filtered for campaign={campaign}")

        if df.empty:
            self._log(f"DataAgent: no data for campaign '{campaign}'", level="warning")
            return pd.DataFrame(columns=["date", metric, "spend", "impressions", "clicks"])

        # Check metric exists
        if metric not in df.columns:
            self._log(f"DataAgent: metric '{metric}' missing, returning empty ts.", level="warning")
            return pd.DataFrame(columns=["date", metric])

        # Resample
        try:
            ts = (
                df.set_index("date")
                .resample(freq)
                .agg({
                    metric: "mean",
                    "spend": "sum" if "spend" in df else "sum",
                    "impressions": "sum" if "impressions" in df else "sum",
                    "clicks": "sum" if "clicks" in df else "sum",
                })
                .reset_index()
            )

            self._log(f"DataAgent: produced time-series {len(ts)} rows (metric={metric})")
            return ts

        except Exception as e:
            self._log(f"DataAgent: ERROR generating time-series: {e}", level="error")
            return pd.DataFrame(columns=["date", metric])

    # ---------------------------------------------------------
    def get_creatives_sample(self, n: int = 20) -> List[Dict[str, Any]]:
        """
        Returns n creatives for CreativeAgent generation.
        Dedupes creative messages + removes NaNs.
        """

        df = self.load()

        cols = ["campaign_name", "adset_name", "creative_type", "creative_message", "ctr"]

        for c in cols:
            if c not in df.columns:
                self._log(f"DataAgent: creative column '{c}' missing.", level="warning")

        # Clean sample
        sample = (
            df[cols]
            .dropna()
            .drop_duplicates(subset=["creative_message"])
            .head(n)
            .to_dict(orient="records")
        )

        self._log(f"DataAgent: creative sample generated → {len(sample)} creatives.")

        return sample
