# src/orchestrator/orchestrator.py
"""
Orchestrator for Kasparro Agentic FB Analyst.

Responsibilities:
- Load config
- Initialize LLM client + agents
- Run Planner -> Insight -> Evaluator -> Creative pipeline
- Log every run to logs/
- Persist artifacts to reports/
"""

import os
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Set

# relative imports (package style)
from ..utils.logger import get_logger

from ..agents.planner import PlannerAgent
from ..agents.data_agent import DataAgent
from ..agents.insight_agent import InsightAgent
from ..agents.evaluator import EvaluatorAgent
from ..agents.creative_agent import CreativeAgent
from ..utils.llm import LLMClient


def _safe_mkdir(path: str):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


class Orchestrator:
    def __init__(self, config_path: str = "config/config.yaml"):
        # Load config
        with open(config_path, "r", encoding="utf-8") as f:
            self.config: Dict[str, Any] = yaml.safe_load(f) or {}

        # Paths
        self.out_dir = Path(self.config.get("paths", {}).get("out_dir", "reports"))
        _safe_mkdir(str(self.out_dir))

        self.logs_dir = Path(self.config.get("paths", {}).get("logs_dir", "logs"))
        _safe_mkdir(str(self.logs_dir))

        # create a default run_id for logger when needed
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
        # attach logger to LLM client so prompts/responses get logged (if LLMClient supports logger)
        try:
            setattr(self.llm_client, "logger", self.logger)
        except Exception:
            pass

        # Initialize agents (pass prompts paths from config if available)
        prompts_cfg = self.config.get("prompts", {})
        self.planner = PlannerAgent(self.llm_client, prompt_path=prompts_cfg.get("planner", "prompts/planner.md"))
        data_path = self.config.get("paths", {}).get("data", "data/sample_dataset.csv")
        self.data_agent = DataAgent(data_path)
        self.insight_agent = InsightAgent(self.llm_client, prompt_path=prompts_cfg.get("insight", "prompts/insight.md"))
        self.evaluator = EvaluatorAgent(self.config)
        self.creative_agent = CreativeAgent(self.llm_client, prompt_path=prompts_cfg.get("creative", "prompts/creative.md"))

        self.logger.info("Orchestrator initialized")
        self.logger.debug(f"Config loaded keys: {list(self.config.keys())}")

    # simple dependency resolution (topological order) for tasks with depends_on field
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
                # cycle detected; break by skipping further recursion
                self.logger.warning(f"Cycle detected in task dependencies at {node_id}; ignoring cyclic edge")
                return
            temp.add(node_id)
            for d in deps.get(node_id, []):
                if d in id_to_task:
                    visit(d)
            temp.remove(node_id)
            visited.add(node_id)
            ordered.append(id_to_task[node_id])

        # ensure deterministic ordering using priority then id
        sorted_tasks = sorted(tasks, key=lambda x: (getattr(x, "priority", 100), x.id))
        for t in sorted_tasks:
            visit(t.id)
        # ensure any missed tasks are appended
        for t in sorted_tasks:
            if t not in ordered:
                ordered.append(t)
        return ordered

    def _write_json(self, path: Path, data: Any):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def run(self, query: str) -> Dict[str, str]:
        # create a fresh run id / logger for this run
        run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        self.run_id = run_id
        self.logger = get_logger(run_id, logs_dir=str(self.logs_dir))
        # attach logger to llm client (if possible)
        try:
            setattr(self.llm_client, "logger", self.logger)
        except Exception:
            pass

        self.logger.info("=== Starting Orchestrator run ===")
        self.logger.info(f"Query: {query}")

        run_meta: Dict[str, Any] = {
            "run_id": run_id,
            "query": query,
            "timestamp": datetime.utcnow().isoformat(),
            "git_commit": None,
            "errors": [],
            "tasks_executed": [],
        }

        # Step 1: Data summary
        try:
            self.logger.info("Generating data summary")
            data_summary = self.data_agent.summary()
            self.logger.debug(f"Data summary keys: {list(data_summary.keys())}")
        except Exception as e:
            self.logger.exception("DataAgent.summary() failed")
            raise RuntimeError(f"DataAgent.summary() failed: {e}")

        # Step 2: Planner
        try:
            self.logger.info("Running PlannerAgent")
            plan = self.planner.plan(query, data_summary)
            num_tasks = len(getattr(plan, "tasks", []))
            run_meta["plan_summary"] = {"num_tasks": num_tasks}
            self.logger.info(f"Planner produced {num_tasks} tasks")
        except Exception as e:
            self.logger.exception("PlannerAgent.plan() failed")
            raise RuntimeError(f"PlannerAgent.plan() failed: {e}")

        ordered_tasks = self._order_tasks(plan.tasks)

        insights_results: List[Dict[str, Any]] = []
        creatives_results: List[Dict[str, Any]] = []

        # Step 3: Execute tasks
        for task in ordered_tasks:
            self.logger.info(f"Executing task {task.id} ({getattr(task,'name', '')})")
            task_dict = task.dict()
            run_meta["tasks_executed"].append({
                "task_id": task.id,
                "name": getattr(task, "name", ""),
                "scope": getattr(task, "scope", ""),
                "priority": getattr(task, "priority", None)
            })

            # Insight generation
            try:
                self.logger.info(f"Generating insights for task {task.id}")
                hypotheses = self.insight_agent.generate(data_summary, task_dict)
                self.logger.info(f"InsightAgent returned {len(hypotheses)} hypotheses for task {task.id}")
            except Exception as e:
                self.logger.exception(f"Insight generation failed for task {task.id}")
                hypotheses = []
                run_meta["errors"].append({"stage": "insight_generate", "task_id": task.id, "error": str(e)})

            # Evaluate hypotheses
            for hyp in hypotheses:
                hyp_id = getattr(hyp, "id", None)
                try:
                    self.logger.info(f"Evaluating hypothesis {hyp_id} (driver={getattr(hyp,'driver',None)})")
                    validation = self.evaluator.validate(hyp, self.data_agent)
                except Exception as e:
                    self.logger.exception(f"Evaluator error for hypothesis {hyp_id}")
                    run_meta["errors"].append({"stage": "evaluator.validate", "task_id": task.id, "hypothesis_id": hyp_id, "error": str(e)})
                    # create an INCONCLUSIVE record
                    validation = {
                        "hypothesis_id": hyp_id,
                        "validation": {"error": str(e)},
                        "confidence_final": 0.0,
                        "status": "INCONCLUSIVE",
                        "notes": f"Evaluator error: {e}"
                    }

                # normalize to dict
                vdict = validation.dict() if hasattr(validation, "dict") else validation
                # attach hypothesis metadata for clarity
                vdict["hypothesis_text"] = getattr(hyp, "hypothesis", "")
                vdict["driver"] = getattr(hyp, "driver", None)
                vdict["supporting_data_points"] = getattr(hyp, "supporting_data_points", [])
                insights_results.append(vdict)

                # log evaluation summary
                self.logger.info(f"Hypothesis {hyp_id} -> status={vdict.get('status')} confidence={vdict.get('confidence_final')}")

                # Creative generation on validated creative_fatigue
                try:
                    status = str(vdict.get("status", "")).upper()
                    driver = vdict.get("driver") or getattr(hyp, "driver", None)
                    if status == "VALIDATED" and driver == "creative_fatigue":
                        self.logger.info(f"Generating creatives for validated creative_fatigue (hypothesis={hyp_id})")
                        # pick campaign scope if available
                        campaign_scope = getattr(task, "scope", None) if getattr(task, "scope", None) and task.scope != "all_campaigns" else None
                        sample_creatives = self.data_agent.get_creatives_sample(n=20)
                        cands = self.creative_agent.generate_for_campaign(campaign_scope or "campaign_1", sample_creatives, n=4)
                        for c in cands:
                            creatives_results.append(c.dict() if hasattr(c, "dict") else c)
                        self.logger.info(f"Generated {len(cands)} creatives for hypothesis {hyp_id}")
                except Exception as e:
                    self.logger.exception(f"Creative generation failed for hypothesis {hyp_id}")
                    run_meta["errors"].append({"stage": "creative_generate", "task_id": task.id, "hypothesis_id": hyp_id, "error": str(e)})

        # Step 4: persist artifacts
        insights_path = self.out_dir / f"insights_{run_id}.json"
        creatives_path = self.out_dir / f"creatives_{run_id}.json"
        report_path = self.out_dir / f"report_{run_id}.md"
        metadata_path = self.out_dir / f"run_metadata_{run_id}.json"

        self.logger.info("Writing artifacts to disk")
        try:
            self._write_json(insights_path, insights_results)
            self._write_json(creatives_path, creatives_results)
        except Exception:
            self.logger.exception("Failed to write insights/creatives JSON")

        # Step 5: build report (best-effort)
        try:
            self._build_report(report_path, query, run_id, insights_results, creatives_results, data_summary, insights_path, creatives_path)
            self.logger.info(f"Report written: {report_path}")
        except Exception as e:
            self.logger.exception("Report generation failed")
            run_meta["errors"].append({"stage": "build_report", "error": str(e)})

        # finalize metadata and write
        run_meta["artifacts"] = {
            "insights": str(insights_path),
            "creatives": str(creatives_path),
            "report": str(report_path),
        }
        try:
            self._write_json(metadata_path, run_meta)
        except Exception:
            self.logger.exception("Failed to write run metadata")

        self.logger.info("=== Run completed ===")
        return {"insights": str(insights_path), "creatives": str(creatives_path), "report": str(report_path), "metadata": str(metadata_path)}

    def _build_report(self, report_path: Path, query: str, run_id: str,
                      insights: List[Dict[str, Any]], creatives: List[Dict[str, Any]],
                      data_summary: Dict[str, Any], insights_path: Path, creatives_path: Path):
        """
        Create a readable markdown report with executive summary, validated/refuted/inconclusive insights,
        creative recommendations, actionable items, and appendix.
        """
        validated = [i for i in insights if str(i.get("status", "")).upper() == "VALIDATED"]
        refuted = [i for i in insights if str(i.get("status", "")).upper() == "REFUTED"]
        inconc = [i for i in insights if str(i.get("status", "")).upper() == "INCONCLUSIVE"]

        def fmt_insight(i: Dict[str, Any]) -> str:
            val = i.get("validation", {}) or {}
            hyp_id = i.get("hypothesis_id", i.get("hypothesis_id", "unknown"))
            driver = i.get("driver", i.get("driver", "unknown"))
            return f"""### 🔍 Hypothesis `{hyp_id}`
**Driver:** {driver}  
**Status:** **{i.get("status", "INCONCLUSIVE")}**  
**Confidence:** {round(i.get("confidence_final", 0.0), 2)}

**Key Metrics:**  
- Baseline: {val.get("baseline_mean", 'N/A')}  
- Test: {val.get("test_mean", 'N/A')}  
- Relative Change (%): {val.get("relative_change_pct", 'N/A')}  
- p-value: {val.get("p_value", 'N/A')}  
- Effect Size (d): {val.get("effect_size", 'N/A')}

**Notes:**  
{i.get("notes", "")}

**Supporting data points:** {i.get("supporting_data_points", [])}

---
"""

        def fmt_creative(c: Dict[str, Any]) -> str:
            return f"""### 🎨 `{c.get("creative_id", "unknown")}` — {c.get("creative_type", "")}
**Headline:** {c.get("headline","")}  
**Body:** {c.get("body","")}  
**CTA:** {c.get("cta","")}  
**Rationale:** {c.get("rationale","")}

---
"""

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# 📊 Kasparro FB Ads Intelligence Report\n")
            f.write(f"**Run ID:** `{run_id}`  \n")
            f.write(f"**Query:** {query}  \n")
            f.write(f"**Date:** {datetime.utcnow().isoformat()}  \n\n")
            f.write("---\n\n")

            # Executive summary
            f.write("## 🚀 Executive Summary\n")
            f.write(f"- Tasks planned: {len(data_summary.get('top_campaigns_by_spend', {}))} (derived from data summary)  \n")
            f.write(f"- Insights validated: **{len(validated)}**  \n")
            f.write(f"- Insights inconclusive: **{len(inconc)}**  \n")
            f.write(f"- Insights refuted: **{len(refuted)}**  \n\n")
            f.write("---\n\n")

            # Validated
            f.write("## ✅ Validated Insights\n")
            if validated:
                for it in validated:
                    f.write(fmt_insight(it))
            else:
                f.write("*No validated insights.*\n\n")
            f.write("\n---\n\n")

            # Inconclusive
            f.write("## ⚠️ Inconclusive Insights\n")
            if inconc:
                for it in inconc:
                    f.write(fmt_insight(it))
            else:
                f.write("*None inconclusive.*\n\n")
            f.write("\n---\n\n")

            # Refuted
            f.write("## ❌ Refuted Insights\n")
            if refuted:
                for it in refuted:
                    f.write(fmt_insight(it))
            else:
                f.write("*None refuted.*\n\n")
            f.write("\n---\n\n")

            # Creatives
            f.write("## 🎨 Creative Recommendations\n")
            if creatives:
                for c in creatives:
                    f.write(fmt_creative(c))
            else:
                f.write("*No creatives generated.*\n\n")
            f.write("\n---\n\n")

            # Actionable recommendations
            f.write("## 🧭 Actionable Recommendations\n")
            if validated:
                f.write("- Rotate creatives for fatigued ads; A/B test 3 new headlines and 2 CTAs.  \n")
                f.write("- Reallocate a portion of spend to the top-performing campaigns while testing creatives.  \n")
                f.write("- Investigate audience overlap and reduce frequency if audience saturation suspected.  \n")
                f.write("- Run platform-specific tests (Instagram vs Facebook) if platform split shows divergence.  \n")
            else:
                f.write("- No high-confidence drivers detected. Continue monitoring and collect more data.  \n")
            f.write("\n---\n\n")

            # Appendix
            f.write("## 📚 Appendix\n")
            f.write(f"- Insights JSON: `{insights_path}`  \n")
            f.write(f"- Creatives JSON: `{creatives_path}`  \n")
            f.write(f"- Data summary (partial):\n\n```\n{json.dumps({k: data_summary.get(k) for k in ['n_rows','date_min','date_max','campaign_count','top_campaigns_by_spend']}, indent=2)}\n```\n")
            f.write("\n---\n")
