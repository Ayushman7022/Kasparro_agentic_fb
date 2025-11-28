# src/agents/evaluator.py

from typing import Dict, Any
import numpy as np
from scipy import stats
from datetime import datetime
import traceback

from src.utils.schemas import ValidationResult

# -------------------------
# DEFAULTS
# -------------------------

DEFAULTS = {
    "p_value_threshold": 0.05,
    "ctr_drop_pct_threshold": 20.0,   # %
    "min_samples_for_ttest": 10,
    "bootstrap_iters": 2000,
    "rolling_window_days": 7,
    "change_point_relative_threshold": 0.15
}

# -------------------------
# UTILITY METHODS
# -------------------------

def cohen_d(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = a.size, b.size

    if na + nb - 2 <= 0:
        return 0.0

    var_a = np.nanvar(a, ddof=1) if na > 1 else 0.0
    var_b = np.nanvar(b, ddof=1) if nb > 1 else 0.0

    pooled_sd = np.sqrt(((na - 1) * var_a + (nb - 1) * var_b) / max(1, (na + nb - 2)))
    if pooled_sd == 0:
        return 0.0

    return float((np.nanmean(b) - np.nanmean(a)) / pooled_sd)


def bootstrap_pvalue(a: np.ndarray, b: np.ndarray, n_iter: int = 2000, seed: int = 42) -> float:
    rng = np.random.default_rng(seed)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)

    combined = np.concatenate([a, b])
    n_a = a.size

    obs_diff = np.nanmean(b) - np.nanmean(a)
    diffs = []

    for _ in range(n_iter):
        sample = rng.choice(combined, size=combined.size, replace=True)
        diffs.append(np.nanmean(sample[n_a:]) - np.nanmean(sample[:n_a]))

    diffs = np.asarray(diffs)
    return float(np.mean(np.abs(diffs) >= abs(obs_diff)))


def simple_change_point(ts_values: np.ndarray, window: int = 7) -> Dict[str, Any]:
    res = {"best_split": None, "relative_change": 0.0, "method_note": ""}

    if ts_values is None or len(ts_values) < (2 * window):
        res["method_note"] = "timeseries too short for change-point heuristic"
        return res

    n = len(ts_values)
    best_rel, best_idx = 0.0, None

    for idx in range(window, n - window):
        left_mean = np.nanmean(ts_values[idx - window:idx])
        right_mean = np.nanmean(ts_values[idx:idx + window])

        if np.isnan(left_mean) or left_mean == 0:
            continue

        rel = (right_mean - left_mean) / left_mean

        if abs(rel) > abs(best_rel):
            best_rel = rel
            best_idx = idx

    res["best_split"] = best_idx
    res["relative_change"] = float(best_rel)
    res["method_note"] = f"rolling-window({window}) heuristic"
    return res

# -------------------------
# MAIN AGENT
# -------------------------

class EvaluatorAgent:
    """
    Evaluates hypotheses using CTR timeseries and statistical tests.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        thresh = self.config.get("thresholds", {})

        self.logger = None  # Will be attached by orchestrator
        # evaluator does NOT receive llm_client, so orchestrator must manually copy logger

        # thresholds
        self.p_value_threshold = thresh.get("p_value_threshold", DEFAULTS["p_value_threshold"])
        self.ctr_drop_pct_threshold = thresh.get("ctr_drop_pct", DEFAULTS["ctr_drop_pct_threshold"])
        self.min_samples_for_ttest = thresh.get("min_samples_for_ttest", DEFAULTS["min_samples_for_ttest"])
        self.bootstrap_iters = thresh.get("bootstrap_iters", DEFAULTS["bootstrap_iters"])
        self.rolling_window_days = thresh.get("rolling_window_days", DEFAULTS["rolling_window_days"])
        self.change_point_relative_threshold = thresh.get("change_point_relative_threshold", DEFAULTS["change_point_relative_threshold"])

    # --------------------------------------------------------
    def _prepare_series(self, data_agent, scope: str, metric: str = "ctr"):
        if self.logger:
            self.logger.info(f"Evaluator: preparing time series for metric={metric}, scope={scope}")

        try:
            ts = data_agent.get_time_series(
                campaign=scope if scope and scope != "all_campaigns" else None,
                metric=metric
            )

            if metric not in ts.columns:
                if self.logger:
                    self.logger.warning("Evaluator: metric missing in dataset.")
                return np.array([]), "metric_missing"

            return ts[metric].astype(float).values, None

        except Exception as e:
            if self.logger:
                self.logger.error(f"Evaluator: error preparing series → {e}")
            return np.array([]), f"error_preparing_series: {e}"

    # --------------------------------------------------------
    def _split_baseline_test(self, values: np.ndarray):
        n = len(values)
        idx = max(1, int(n * 0.7))
        return values[:idx], values[idx:]

    # --------------------------------------------------------
    def _decide_status(self, p_value: float, relative_change_pct: float, effect_size: float, n_total: int) -> str:
        if self.logger:
            self.logger.info(
                f"Evaluator decision logic: p={p_value}, rel={relative_change_pct}, d={effect_size}, n={n_total}"
            )

        # strong evidence
        if p_value < 0.01 and abs(effect_size) >= 0.5 and n_total >= 30:
            return "VALIDATED"

        # medium evidence
        if p_value < self.p_value_threshold and abs(relative_change_pct) >= self.ctr_drop_pct_threshold:
            return "VALIDATED"

        # weak significance
        if p_value < self.p_value_threshold:
            return "INCONCLUSIVE"

        # no significance
        return "REFUTED"

    # --------------------------------------------------------
    def _calibrate_confidence(self, initial, p_value, effect_size, n_total):
        conf = float(initial)

        if p_value < 0.01:
            conf += 0.25
        elif p_value < 0.05:
            conf += 0.12
        else:
            conf -= 0.12

        if abs(effect_size) >= 0.8:
            conf += 0.2
        elif abs(effect_size) >= 0.5:
            conf += 0.1

        if n_total < 30:
            conf -= 0.15

        return max(0.0, min(1.0, conf))

    # --------------------------------------------------------
    def validate(self, hypothesis, data_agent) -> ValidationResult:

        hyp_id = getattr(hypothesis, "id", "unknown")
        driver = getattr(hypothesis, "driver", None)
        initial_conf = getattr(hypothesis, "initial_confidence", 0.5)

        if self.logger:
            self.logger.info(f"Evaluator: validating hypothesis {hyp_id} (driver={driver})")

        try:
            # ----------------------------------
            # use CTR for creative_fatigue / metric_check
            # ----------------------------------
            scope = "all_campaigns"
            values, err = self._prepare_series(data_agent, scope, metric="ctr")

            if err or values.size < 2:
                if self.logger:
                    self.logger.warning(f"Evaluator: insufficient CTR data for {hyp_id}: {err}")

                return ValidationResult(
                    hypothesis_id=hyp_id,
                    validation={"error": err or "too few samples"},
                    confidence_final=0.2,
                    status="INCONCLUSIVE",
                    notes=f"Insufficient data for evaluation (driver={driver})",
                    evidence_refs=[]
                )

            baseline, test = self._split_baseline_test(values)
            n_total = len(baseline) + len(test)

            # ----------------------------------
            # statistical tests
            # ----------------------------------
            if len(baseline) >= self.min_samples_for_ttest and len(test) >= self.min_samples_for_ttest:
                tstat, p_value = stats.ttest_ind(baseline, test, equal_var=False, nan_policy="omit")
                p_value = float(p_value)
                method = "t-test"
            else:
                p_value = bootstrap_pvalue(baseline, test, n_iter=self.bootstrap_iters)
                method = "bootstrap"

            baseline_mean = float(np.nanmean(baseline))
            test_mean = float(np.nanmean(test))
            relative_change_pct = ((test_mean - baseline_mean) / max(1e-9, baseline_mean)) * 100
            effect_size = cohen_d(baseline, test)

            # change point
            cp = simple_change_point(values, window=self.rolling_window_days)

            if self.logger:
                self.logger.info(
                    f"Evaluator stats → p={p_value}, rel={relative_change_pct:.2f}%, d={effect_size}, baseline={baseline_mean}, test={test_mean}"
                )

            status = self._decide_status(p_value, relative_change_pct, effect_size, n_total)
            final_conf = self._calibrate_confidence(initial_conf, p_value, effect_size, n_total)

            result = ValidationResult(
                hypothesis_id=hyp_id,
                validation={
                    "metric": "ctr",
                    "method": method,
                    "baseline_mean": baseline_mean,
                    "test_mean": test_mean,
                    "relative_change_pct": relative_change_pct,
                    "p_value": p_value,
                    "effect_size": effect_size,
                    "n_baseline": len(baseline),
                    "n_test": len(test),
                    "change_point": cp
                },
                confidence_final=final_conf,
                status=status,
                notes=f"Evaluated driver={driver} using {method}",
                evidence_refs=[]
            )

            if self.logger:
                self.logger.info(f"Evaluator: hypothesis {hyp_id} → {status} (conf={final_conf})")

            return result

        except Exception as e:
            if self.logger:
                self.logger.error(f"Evaluator: exception during evaluation of {hyp_id} → {e}")
                self.logger.error(traceback.format_exc())

            return ValidationResult(
                hypothesis_id=hyp_id,
                validation={"error": str(e)},
                confidence_final=0.1,
                status="INCONCLUSIVE",
                notes=f"Exception during evaluation: {e}",
                evidence_refs=[]
            )
