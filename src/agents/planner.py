# src/agents/planner.py

from datetime import datetime
from typing import Dict, Any
import json, uuid

from src.utils.llm import LLMClient
from src.utils.parse_utils import extract_json_from_text
from src.utils.schemas import PlanOutput, TaskSchema


class PlannerAgent:
    def __init__(self, llm_client: LLMClient,
                 prompt_path: str = "prompts/planner.md",
                 retries: int = 2):

        self.logger = None  # type: ignore

        self.llm = llm_client
        self.logger = getattr(llm_client, "logger", None)     # <-- LOGGER ADDED

        self.prompt_path = prompt_path
        self.retries = retries

        with open(prompt_path, "r", encoding="utf-8") as f:
            self.template = f.read()

        if self.logger:
            self.logger.info("PlannerAgent initialized.")

    # -------------------------------------------
    def _build_prompt(self, query: str, data_summary: Dict[str, Any]) -> str:
        if self.logger:
            self.logger.info("PlannerAgent: building planning prompt.")

        prompt = self.template
        prompt += "\n\nUSER_QUERY:\n" + query + "\n"
        prompt += "\nDATA_SUMMARY:\n" + json.dumps(data_summary, indent=2)
        prompt += "\n\nReturn JSON only."
        return prompt

    # -------------------------------------------
    def plan(self, query: str, data_summary: Dict[str, Any]) -> PlanOutput:

        if self.logger:
            self.logger.info("PlannerAgent: generating plan from LLM.")

        prompt = self._build_prompt(query, data_summary)

        # ---- Gemini call ----
        resp = self.llm.safe_generate(prompt, retries=self.retries)
        text = resp.get("text", "")

        if self.logger:
            self.logger.debug(f"PlannerAgent: LLM raw output snippet: {text[:600]}")

        # ---- Extract JSON ----
        parsed, err = extract_json_from_text(text)

        if parsed is None:
            if self.logger:
                self.logger.warning("PlannerAgent: JSON parsing failed. Using fallback plan.")
            return self._fallback_plan(query)

        try:
            tasks = []
            for t in parsed.get("tasks", []):
                tasks.append(
                    TaskSchema(
                        id=t.get("id", "t_" + str(uuid.uuid4())[:8]),
                        name=t.get("name", "analysis_task"),
                        type=t.get("type", "metric_check"),
                        target=t.get("target", "roas"),
                        scope=t.get("scope", "all_campaigns"),
                        priority=int(t.get("priority", 5)),
                        depends_on=t.get("depends_on", []),
                        expected_schema=t.get("expected_output_schema", {})
                    )
                )

            if self.logger:
                self.logger.info(f"PlannerAgent: built {len(tasks)} tasks from LLM.")

            return PlanOutput(
                query=parsed.get("query", query),
                generated_at=datetime.utcnow(),
                plan_description=parsed.get("plan_description", ""),
                tasks=tasks
            )

        except Exception as e:
            if self.logger:
                self.logger.error(f"PlannerAgent: validation error — {e}")

            return self._fallback_plan(query)

    # -------------------------------------------
    def _fallback_plan(self, query: str) -> PlanOutput:
        if self.logger:
            self.logger.warning("PlannerAgent: generating fallback plan.")

        return PlanOutput(
            query=query,
            generated_at=datetime.utcnow(),
            plan_description="Fallback plan due to JSON parse failure.",
            tasks=[
                TaskSchema(
                    id="t1",
                    name="roas_time_series",
                    type="timeseries",
                    target="roas",
                    scope="all_campaigns",
                    priority=1,
                    depends_on=[],
                    expected_schema={"fields": ["date", "roas", "spend"]}
                ),
                TaskSchema(
                    id="t2",
                    name="ctr_check",
                    type="metric_check",
                    target="ctr",
                    scope="all_campaigns",
                    priority=2,
                    depends_on=["t1"],
                    expected_schema={"fields": ["date", "ctr", "impressions"]}
                )
            ]
        )
