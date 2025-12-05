# src/agents/insight_agent.py

from typing import List, Dict, Any, Optional
import json
import uuid
import math
import statistics
from datetime import datetime

from src.utils.llm import LLMClient
from src.utils.parse_utils import extract_json_from_text
from src.utils.schemas import Hypothesis


# simple change-point heuristic
def _simple_change_point(values: List[float], window: int = 7) -> Dict[str, Any]:
    if values is None or len(values) < 2 * max(1, window):
        return {"best_split": None, "relative_change": 0.0, "method_note": "series too short"}

    n = len(values)
    best_rel = 0.0
    best_idx = None

    for idx in range(window, n - window):
        left = values[max(0, idx - window): idx]
        right = values[idx: min(n, idx + window)]
        if not left or not right:
            continue

        left_mean = statistics.mean(left)
        right_mean = statistics.mean(right)
        if left_mean == 0:
            continue

        rel = (right_mean - left_mean) / left_mean
        if abs(rel) > abs(best_rel):
            best_rel = rel
            best_idx = idx

    return {
        "best_split": int(best_idx) if best_idx is not None else None,
        "relative_change": float(best_rel),
        "method_note": f"rolling-window {window}"
    }


class InsightAgent:
    """
    InsightAgent: generates hypotheses grounded in data.
    """

    def __init__(self, llm_client: LLMClient,
                 prompt_path: str = "prompts/insight.md",
                 retries: int = 2,
                 ctr_drop_threshold: float = 0.15):

        self.llm = llm_client
        self.prompt_path = prompt_path
        self.retries = retries
        self.ctr_drop_threshold = ctr_drop_threshold

        with open(prompt_path, "r", encoding="utf-8") as f:
            self.template = f.read()

        # logger if attached externally
        self.logger = getattr(self.llm, "logger", None)

    def _build_prompt(self, task: Dict[str, Any], data_summary: Dict[str, Any], local_evidence: Dict[str, Any]) -> str:
        sections = [self.template]
        sections.append("\n\nTASK:\n" + json.dumps(task, indent=2))
        sections.append("\n\nDATA_SUMMARY:\n" + json.dumps(data_summary, indent=2))
        sections.append("\n\nLOCAL_EVIDENCE:\n" + json.dumps(local_evidence, indent=2))
        sections.append("\n\nINSTRUCTIONS:\nReturn ONLY a JSON array of hypotheses with fields: id, hypothesis, driver, initial_confidence, supporting_data_points, required_checks.")
        return "\n".join(sections)

    def _compose_data_evidence(self, data_agent, task: Dict[str, Any]) -> Dict[str, Any]:
        metric = task.get("target", "ctr")
        scope = task.get("scope", None) if task.get("scope") and task.get("scope") != "all_campaigns" else None

        try:
            ts = data_agent.get_time_series(campaign=scope, metric=metric, freq="D")
            if ts is None or ts.empty or metric not in ts.columns:
                return {"note": "timeseries_missing_or_empty", "metric": metric}

            values = ts[metric].astype(float).fillna(method="ffill").fillna(method="bfill").tolist()
            n = len(values)
            if n < 2:
                return {"note": "timeseries_too_short", "metric": metric, "n": n}

            split_idx = max(1, int(n * 0.7))
            baseline_vals = values[:split_idx]
            test_vals = values[split_idx:]

            baseline_mean = statistics.mean(baseline_vals) if baseline_vals else 0.0
            test_mean = statistics.mean(test_vals) if test_vals else 0.0
            delta_pct = ((test_mean - baseline_mean) / baseline_mean) * 100.0 if baseline_mean not in (0, None) else None

            cp = _simple_change_point(values, window=7)

            return {
                "metric": metric,
                "n_total": n,
                "n_baseline": len(baseline_vals),
                "n_test": len(test_vals),
                "baseline_mean": baseline_mean,
                "test_mean": test_mean,
                "relative_change_pct": delta_pct,
                "change_point": cp
            }

        except Exception as e:
            return {"note": "timeseries_exception", "error": str(e), "metric": metric}

    def generate(self, data_summary: Dict[str, Any], task: Dict[str, Any]) -> List[Hypothesis]:

        data_agent = getattr(self, "data_agent", None)

        # === Log starting step ===
        if self.logger:
            self.logger.info(f"InsightAgent: generating hypotheses for task {task.get('id')} ({task.get('name')})")

        # === Compute evidence ===
        local_evidence = self._compose_data_evidence(data_agent, task) if data_agent else {}

        if self.logger:
            self.logger.debug(f"InsightAgent: local evidence → {local_evidence}")

        generated_hypotheses: List[Hypothesis] = []

        try:
            # === 1) Data-driven hypothesis ===
            rel = local_evidence.get("relative_change_pct")
            metric = local_evidence.get("metric", task.get("target", "ctr"))

            if rel is not None and isinstance(rel, (int, float)) and abs(rel) >= (self.ctr_drop_threshold * 100):

                hyp_text = (
                    f"{metric.upper()} dropped by {rel:.1f}% for scope `{task.get('scope', 'all_campaigns')}`, "
                    "indicating possible creative fatigue or audience saturation."
                )

                if self.logger:
                    self.logger.info(f"InsightAgent: structured hypothesis triggered → {hyp_text}")

                initial_conf = min(0.9, 0.5 + min(0.4, abs(rel) / 100.0))

                supporting = [{
                    "type": "timeseries_delta",
                    "metric": metric,
                    "baseline": local_evidence.get("baseline_mean"),
                    "current": local_evidence.get("test_mean"),
                    "delta_pct": rel,
                }]

                generated_hypotheses.append(
                    Hypothesis(
                        id="hyp_" + uuid.uuid4().hex[:8],
                        hypothesis=hyp_text,
                        driver="creative_fatigue" if metric == "ctr" else "metric_drop",
                        initial_confidence=initial_conf,
                        supporting_data_points=supporting,
                        required_checks=["ctr_time_series", "creative_impressions_split"]
                    )
                )

            # === 2) LLM hypotheses ===
            prompt = self._build_prompt(task, data_summary, local_evidence)

            if self.logger:
                self.logger.debug(f"InsightAgent prompt preview: {prompt[:400].replace(chr(10), ' ')}")

            resp = self.llm.safe_generate(prompt, retries=self.retries)
            text = resp.get("text", "")

            if self.logger:
                self.logger.debug(f"InsightAgent LLM raw output preview: {text[:400]}")

            parsed, err = extract_json_from_text(text)

            if isinstance(parsed, list):
                for raw in parsed:
                    try:
                        generated_hypotheses.append(
                            Hypothesis(
                                id=raw.get("id", "hyp_" + uuid.uuid4().hex[:8]),
                                hypothesis=raw.get("hypothesis", raw.get("text", "")),
                                driver=raw.get("driver", "other"),
                                initial_confidence=float(raw.get("initial_confidence", 0.5)),
                                supporting_data_points=raw.get("supporting_data_points", []) or [],
                                required_checks=raw.get("required_checks", []) or [],
                            )
                        )
                    except Exception:
                        continue

        except Exception as e:
            if self.logger:
                self.logger.exception(f"InsightAgent LLM call failed: {e}")

        # === Fallback ===
        if not generated_hypotheses:
            fallback = Hypothesis(
                id="hyp_" + uuid.uuid4().hex[:8],
                hypothesis=f"Possible anomaly detected in `{task.get('target', 'metric')}` for `{task.get('scope', 'all_campaigns')}`.",
                driver="other",
                initial_confidence=0.4,
                supporting_data_points=[{"note": "fallback - no LLM output or insufficient evidence"}],
                required_checks=[f"{task.get('target', 'metric')}_timeseries"],
            )
            generated_hypotheses.append(fallback)

            if self.logger:
                self.logger.warning("InsightAgent: fallback hypothesis used")

        # === Deduplicate ===
        seen = set()
        unique: List[Hypothesis] = []
        for h in generated_hypotheses:
            if h.hypothesis not in seen:
                seen.add(h.hypothesis)
                unique.append(h)

        if self.logger:
            self.logger.info(f"InsightAgent: final hypotheses generated → {len(unique)}")

        return unique
