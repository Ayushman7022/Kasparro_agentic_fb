# src/agents/insight_agent.py

from typing import List, Dict, Any
import json, uuid

from src.utils.llm import LLMClient
from src.utils.parse_utils import extract_json_from_text
from src.utils.schemas import Hypothesis


class InsightAgent:
    """
    Generates hypotheses for each analysis task.
    Uses LLM and returns structured Hypothesis objects.
    """

    def __init__(self, llm_client: LLMClient,
                 prompt_path: str = "prompts/insight.md",
                 retries: int = 2):
        self.logger = None  # type: ignore

        self.llm = llm_client
        self.logger = getattr(llm_client, "logger", None)   # <-- Attach logger

        self.prompt_path = prompt_path
        self.retries = retries

        with open(prompt_path, "r", encoding="utf-8") as f:
            self.template = f.read()

        if self.logger:
            self.logger.info("InsightAgent initialized.")

    # ---------------------------------------------------------------------
    def _build_prompt(self, data_summary: Dict[str, Any], task: Dict[str, Any]) -> str:
        """Constructs insight-generation prompt."""
        if self.logger:
            self.logger.info("InsightAgent: building hypothesis prompt.")

        prompt = self.template
        prompt += "\n\nTASK:\n" + json.dumps(task, indent=2)
        prompt += "\n\nDATA_SUMMARY:\n" + json.dumps(data_summary, indent=2)
        prompt += "\n\nReturn ONLY a JSON array of hypotheses."
        return prompt

    # ---------------------------------------------------------------------
    def generate(self, data_summary: Dict[str, Any], task: Dict[str, Any]) -> List[Hypothesis]:
        """Produce a list of Hypothesis objects using the LLM."""

        if self.logger:
            self.logger.info(f"InsightAgent: generating hypotheses for task {task.get('id')}")

        prompt = self._build_prompt(data_summary, task)

        # ------------- LLM CALL -------------
        resp = self.llm.safe_generate(prompt, retries=self.retries)
        raw_text = resp.get("text", "")

        if self.logger:
            snippet = raw_text[:800].replace("\n", " ")
            self.logger.debug(f"InsightAgent: LLM hypothesis raw output: {snippet}")

        # ------------- JSON PARSE -------------
        parsed, err = extract_json_from_text(raw_text)

        if err and self.logger:
            self.logger.warning(f"InsightAgent: JSON parse issue detected → {err}")

        hypotheses: List[Hypothesis] = []

        # If parsed correctly and is list -> process hypotheses
        if parsed and isinstance(parsed, list):
            for raw in parsed:
                try:
                    h = Hypothesis(
                        id=raw.get("id", f"hyp_{uuid.uuid4().hex[:8]}"),
                        hypothesis=raw.get("hypothesis", "No hypothesis provided"),
                        driver=raw.get("driver", "other"),
                        initial_confidence=float(raw.get("initial_confidence", 0.5)),
                        supporting_data_points=raw.get("supporting_data_points", []),
                        required_checks=raw.get("required_checks", []),
                    )
                    hypotheses.append(h)
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"InsightAgent: malformed hypothesis skipped: {e}")
                    continue

        # ------------- FALLBACK LOGIC -------------
        if not hypotheses:
            if self.logger:
                self.logger.warning("InsightAgent: No hypotheses parsed. Using fallback.")

            hypotheses.append(
                Hypothesis(
                    id=f"hyp_{uuid.uuid4().hex[:8]}",
                    hypothesis="Possible CTR decline in top campaigns indicating creative fatigue.",
                    driver="creative_fatigue",
                    initial_confidence=0.55,
                    supporting_data_points=["Fallback due to malformed JSON"],
                    required_checks=["ctr_time_series", "creative_impressions_split"]
                )
            )

        # Log final count
        if self.logger:
            self.logger.info(f"InsightAgent: produced {len(hypotheses)} hypotheses.")

        return hypotheses
