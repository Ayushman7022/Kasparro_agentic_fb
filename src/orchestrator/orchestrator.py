# src/orchestrator/orchestrator.py

"""
Orchestrator for Kasparro Agentic FB Analyst.

Responsibilities:
- Load config
- Initialize LLM client + agents
- Run Planner → Insight → Evaluation → Creative pipeline
- Log every run
- Save JSON + Markdown reports
"""
from ..utils.schema_validator import validate_schema, SchemaValidationError

import os
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Set

# internal imports
from ..utils.logger import get_logger

from ..agents.planner import PlannerAgent
from ..agents.data_agent import DataAgent
from ..agents.insight_agent import InsightAgent
from ..agents.evaluator import EvaluatorAgent
from ..agents.creative_agent import CreativeAgent
from ..utils.llm import LLMClient


def _safe_mkdir(path: str):
    """Create directory if missing."""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


class Orchestrator:
    def __init__(self, config_path: str = "config/config.yaml"):

        # Load config
        with open(config_path, "r", encoding="utf-8") as f:
            self.config: Dict[str, Any] = yaml.safe_load(f) or {}

        # Output folders
        self.out_dir = Path(self.config.get("paths", {}).get("out_dir", "reports"))
        _safe_mkdir(str(self.out_dir))

        self.logs_dir = Path(self.config.get("paths", {}).get("logs_dir", "logs"))
        _safe_mkdir(str(self.logs_dir))

        # Init logger
        self.run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        self.logger = get_logger(self.run_id, logs_dir=str(self.logs_dir))

        # Initialize LLM client
        llm_cfg = self.config.get("llm", {})
        self.llm_client = LLMClient(
            provider=llm_cfg.get("provider", "gemini"),
            model=llm_cfg.get("model", "gemini-1.5-flash"),
            temperature=llm_cfg.get("temperature", 0.0),
            max_tokens=llm_cfg.get("max_tokens", 1500),
        )
        setattr(self.llm_client, "logger", self.logger)

        # Initialize agents
        prompts_cfg = self.config.get("prompts", {})
        data_path = self.config.get("paths", {}).get("data", "data/sample_dataset.csv")

        self.planner = PlannerAgent(self.llm_client, prompt_path=prompts_cfg.get("planner", "prompts/planner.md"))
        self.data_agent = DataAgent(data_path)
        self.insight_agent = InsightAgent(self.llm_client, prompt_path=prompts_cfg.get("insight", "prompts/insight.md"))
        self.insight_agent.data_agent = self.data_agent  # IMPORTANT
        self.evaluator = EvaluatorAgent(self.config)
        self.creative_agent = CreativeAgent(self.llm_client, prompt_path=prompts_cfg.get("creative", "prompts/creative.md"))

        self.logger.info("Orchestrator initialized.")

    # -------------------------------------------------------------------------
    # Topological ordering for planner tasks
    # -------------------------------------------------------------------------
    def _order_tasks(self, tasks: List[Any]) -> List[Any]:
        id_to_task = {t.id: t for t in tasks}
        deps = {t.id: set(getattr(t, "depends_on", []) or []) for t in tasks}

        ordered: List[Any] = []
        visited: Set[str] = set()
        temp: Set[str] = set()

        def visit(node_id: str):
            if node_id in visited:
                return
            if node_id in temp:
                self.logger.warning(f"Cycle detected involving {node_id}")
                return
            temp.add(node_id)
            for d in deps.get(node_id, []):
                if d in id_to_task:
                    visit(d)
            temp.remove(node_id)
            visited.add(node_id)
            ordered.append(id_to_task[node_id])

        # stable ordering
        for t in sorted(tasks, key=lambda x: (getattr(x, "priority", 100), x.id)):
            visit(t.id)

        return ordered

    # -------------------------------------------------------------------------
    # Write JSON
    # -------------------------------------------------------------------------
    def _write_json(self, path: Path, data: Any):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    # -------------------------------------------------------------------------
    # MAIN PIPELINE
    # -------------------------------------------------------------------------
    def run(self, query: str) -> Dict[str, str]:

        # fresh run logger
        run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        self.run_id = run_id
        self.logger = get_logger(run_id, logs_dir=str(self.logs_dir))
        setattr(self.llm_client, "logger", self.logger)

        self.logger.info("=== Starting pipeline run ===")
        self.logger.info(f"Query: {query}")

        run_meta = {
            "run_id": run_id,
            "query": query,
            "timestamp": datetime.utcnow().isoformat(),
            "errors": [],
            "tasks_executed": [],
        }

        # Add Git commit hash for V2 evaluator
        try:
            commit = os.popen("git rev-parse HEAD").read().strip()
            if not commit:
                commit = "unknown"
        except Exception:
            commit = "unknown"

        run_meta["git_commit"] = commit

        # ------------------------------
        # 1. DATA SUMMARY
        # ------------------------------
        # Step 0: Schema validation BEFORE summary

        try:
            self.logger.info("Validating dataset schema...")
            df = self.data_agent.load()  # load raw dataset
            schema_path = self.config.get("paths", {}).get("schema", "config/data_schema.yaml")
            validate_schema(df, schema_path, logger=self.logger)
            self.logger.info("Schema validation passed")
        except SchemaValidationError as e:
            self.logger.exception("Schema validation failed")
            raise RuntimeError(f"Schema validation failed: {e}")

        # Step 1: Data summary AFTER schema is known valid
        self.logger.info("Generating data summary")
        data_summary = self.data_agent.summary()
        self.logger.info("Data summary generated")

        # ------------------------------
        # 2. PLANNER
        # ------------------------------
        plan = self.planner.plan(query, data_summary)
        tasks = self._order_tasks(plan.tasks)
        self.logger.info(f"Planner created {len(tasks)} tasks")

        insights_results = []
        creatives_results = []

        # ------------------------------
        # 3. EXECUTE TASKS
        # ------------------------------
        for task in tasks:
            self.logger.info(f"Executing task {task.id} ({getattr(task, 'name', '')})")

            # Insight generation
            try:
                hypotheses = self.insight_agent.generate(data_summary, task.dict())
                self.logger.info(f"Generated {len(hypotheses)} hypotheses")
            except Exception as e:
                self.logger.exception("InsightAgent failed")
                hypotheses = []
                run_meta["errors"].append({"stage": "insight", "task": task.id, "error": str(e)})

            # Evaluate hypotheses
            for hyp in hypotheses:
                hyp_id = getattr(hyp, "id", None)
                try:
                    validation = self.evaluator.validate(hyp, self.data_agent)
                except Exception as e:
                    self.logger.exception("Evaluator failed")
                    validation = {
                        "hypothesis_id": hyp_id,
                        "status": "INCONCLUSIVE",
                        "validation": {"error": str(e)},
                        "confidence_final": 0.0,
                    }

                vdict = validation.dict() if hasattr(validation, "dict") else validation
                vdict["hypothesis_text"] = getattr(hyp, "hypothesis", "")
                vdict["driver"] = getattr(hyp, "driver", None)
                vdict["supporting_data_points"] = getattr(hyp, "supporting_data_points", [])

                insights_results.append(vdict)

                # Creative generation only if validated & driver = creative_fatigue
                if str(vdict.get("status", "")).upper() == "VALIDATED" and vdict.get("driver") == "creative_fatigue":
                    try:
                        sample_creatives = self.data_agent.get_creatives_sample(20)
                        results = self.creative_agent.generate_for_campaign(
                            getattr(task, "scope", None) or "campaign_1",
                            sample_creatives,
                            n=4
                        )
                        for c in results:
                            creatives_results.append(c)
                    except Exception as e:
                        self.logger.exception("CreativeAgent failed")
                        run_meta["errors"].append({"stage": "creative", "hypothesis": hyp_id, "error": str(e)})

        # ------------------------------
        # 4. WRITE ARTIFACTS
        # ------------------------------
        insights_path = self.out_dir / f"insights_{run_id}.json"
        creatives_path = self.out_dir / f"creatives_{run_id}.json"
        report_path = self.out_dir / f"report_{run_id}.md"
        meta_path = self.out_dir / f"run_metadata_{run_id}.json"

        self._write_json(insights_path, insights_results)
        self._write_json(creatives_path, creatives_results)

        # ------------------------------
        # 5. BUILD REPORT
        # ------------------------------
        self._build_report(report_path, query, run_id, insights_results, creatives_results, data_summary, insights_path, creatives_path)

        # Save metadata
        run_meta["artifacts"] = {
            "insights": str(insights_path),
            "creatives": str(creatives_path),
            "report": str(report_path),
        }
        self._write_json(meta_path, run_meta)

        self.logger.info("=== Pipeline completed successfully ===")

        return {
            "insights": str(insights_path),
            "creatives": str(creatives_path),
            "report": str(report_path),
            "metadata": str(meta_path),
        }

    # -------------------------------------------------------------------------
    # REPORT GENERATION
    # -------------------------------------------------------------------------
    def _build_report(self, report_path: Path, query: str, run_id: str,
                      insights: List[Dict[str, Any]], creatives: List[Dict[str, Any]],
                      data_summary: Dict[str, Any], insights_path: Path, creatives_path: Path):

        validated = [i for i in insights if str(i.get("status", "")).upper() == "VALIDATED"]
        refuted = [i for i in insights if str(i.get("status", "")).upper() == "REFUTED"]
        inconclusive = [i for i in insights if str(i.get("status", "")).upper() == "INCONCLUSIVE"]

        # -----------------------
        # Formatting helpers
        # -----------------------
        def fmt_insight(i: Dict[str, Any]) -> str:
            val = i.get("validation", {}) or {}
            ev = i.get("evidence", {}) or {}

            return f"""### 🔍 Hypothesis `{i.get("hypothesis_id")}`
**Driver:** {i.get("driver")}  
**Status:** **{i.get("status")}**  
**Confidence:** {round(i.get("confidence_final", 0.0), 2)}

**Evaluator Metrics**
- Baseline: {val.get("baseline_mean", "N/A")}
- Test: {val.get("test_mean", "N/A")}
- Relative Change (%): {val.get("relative_change_pct", "N/A")}
- p-value: {val.get("p_value", "N/A")}
- Effect Size: {val.get("effect_size", "N/A")}

**Evidence**
- Baseline CTR: {ev.get("baseline_ctr", "N/A")}
- Current CTR: {ev.get("current_ctr", "N/A")}
- CTR Delta %: {ev.get("ctr_delta_pct", "N/A")}
- Change Point: {ev.get("change_point", "N/A")}

**Notes:** {i.get("notes", "")}

---
"""

        def fmt_creative(c: Dict[str, Any]) -> str:
            return f"""### 🎨 Creative `{c.get("creative_id")}`
**Headline:** {c.get("headline")}  
**Body:** {c.get("body")}  
**CTA:** {c.get("cta")}  
**Rationale:** {c.get("rationale")}

---
"""

        # -----------------------
        # WRITE REPORT FILE
        # -----------------------
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# 📊 Kasparro FB Analyst Report\n")
            f.write(f"**Run ID:** `{run_id}`\n")
            f.write(f"**Query:** {query}\n")
            f.write(f"**Timestamp:** {datetime.utcnow().isoformat()}\n\n")

            # Overview
            f.write("## 🚀 Executive Summary\n")
            f.write(f"- Validated insights: **{len(validated)}**\n")
            f.write(f"- Refuted insights: **{len(refuted)}**\n")
            f.write(f"- Inconclusive insights: **{len(inconclusive)}**\n\n")

            # Validated
            f.write("## ✅ Validated Insights\n")
            if validated:
                for it in validated:
                    f.write(fmt_insight(it))
            else:
                f.write("*No validated insights found.*\n\n")

            # Inconclusive
            f.write("## ⚠️ Inconclusive Insights\n")
            if inconclusive:
                for it in inconclusive:
                    f.write(fmt_insight(it))
            else:
                f.write("*None.*\n\n")

            # Refuted
            f.write("## ❌ Refuted Insights\n")
            if refuted:
                for it in refuted:
                    f.write(fmt_insight(it))
            else:
                f.write("*None.*\n\n")

            # Creatives
            f.write("## 🎨 Creative Recommendations\n")
            if creatives:
                for c in creatives:
                    f.write(fmt_creative(c))
            else:
                f.write("*No creatives generated.*\n\n")

            # Appendix
            f.write("## 📚 Appendix\n")
            f.write(f"- Insights JSON: `{insights_path}`\n")
            f.write(f"- Creatives JSON: `{creatives_path}`\n")
            f.write("\n---\n")
