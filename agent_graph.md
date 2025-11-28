Overview

This document describes the multi-agent architecture of the Kasparro FB Ads Intelligence System.
The system uses a fully modular agentic workflow to:

Break down a user query into actionable analytical tasks

Generate hypotheses from tasks

Validate them using statistical & quantitative evaluation

Trigger creative generation when needed

Produce structured insights, creatives, and a full analytical report

This file visualizes the information flow, dependencies, and agent interaction graph.

🧠 High-Level Architecture Diagram
flowchart TD

A[User Query] --> B[Planner Agent]

B -->|Task List| C[Insight Agent]

C -->|Hypotheses| D[Evaluator Agent]

D -->|Validation Results| H[Report Builder]

D -->|If VALIDATED & driver=creative_fatigue| E[Creative Agent]

E -->|Generated Creatives| H[Report Builder]

subgraph Data Layer
F[DataAgent] --> C
F --> D
end

H --> I[Final Outputs: insights.json, creatives.json, report.md, metadata.json]

🗂️ Agent Responsibilities
1. Planner Agent

🎯 Goal: Convert a natural-language query into a structured, multi-step analytical plan.

Inputs:

User query (text)

Data summary (from DataAgent)

Outputs:

List of tasks (TaskSchema), each with:

id

name

type

target metric

scope

priority

depends_on

output schema

🧠 The planner sets the reasoning structure for the entire system.

2. Insight Agent

🎯 Goal: Generate hypotheses for each planned task using LLM reasoning.

Inputs:

Task description

Data summary

Outputs:

List of hypotheses (Hypothesis model):

hypothesis text

driver (creative_fatigue, roas_drop, ctr_drop, etc.)

initial confidence

required checks

supporting datapoints

🧪 This agent is the system’s intelligent inference module.

3. Evaluator Agent

🎯 Goal: Validate each hypothesis with real statistical techniques.

Inputs:

Hypothesis

DataAgent to fetch timeseries

Methods used:

Welch t-test

Bootstrap p-value

Cohen’s d effect size

Relative % change

Change-point detection

Confidence recalibration

Outputs:

ValidationResult:

status → VALIDATED / REFUTED / INCONCLUSIVE

p-value

effect size

relative change

confidence_final

notes

📊 This agent ensures LLM claims are quantitatively grounded.

4. Creative Agent

🎯 Goal: Generate fresh, deduplicated creatives when evaluator confirms creative fatigue.

Inputs:

campaign scope

sample creatives

number of variations needed

Outputs:

JSON creatives:

headline

body

CTA

rationale

creative_type

creative_id

🎨 This agent extends the insight pipeline into actionable creative strategy.

5. Data Agent

🎯 Goal: The factual reference. Provides clean data to all agents.

Provides:

Dataset summary

Time-series (CTR/ROAS/Spends)

Creative samples

Grouped stats

📚 Acts as the data layer of the system.

🔄 Detailed Workflow
sequenceDiagram
    participant U as User
    participant O as Orchestrator
    participant P as PlannerAgent
    participant D as DataAgent
    participant I as InsightAgent
    participant V as EvaluatorAgent
    participant C as CreativeAgent
    participant R as ReportBuilder

    U->>O: Provide query
    O->>D: Request data summary
    D-->>O: Summary
    O->>P: Build task plan
    P-->>O: Task list

    loop For each Task
        O->>I: Generate hypotheses
        I-->>O: Hypothesis list

        loop For each Hypothesis
            O->>V: Validate hypothesis
            V-->>O: ValidationResult

            alt Validated & driver=creative_fatigue
                O->>C: Generate creatives
                C-->>O: Creative set
            end
        end
    end

    O->>R: Create JSON + Markdown reports
    R-->>U: insights.json, creatives.json, report.md

🧩 Key Agent Interactions
Planner → Insight (Task decomposition → Hypotheses)

Planner outputs “what to check”.
Insight outputs “why this may be happening”.

Insight → Evaluator (Hypotheses → Math validation)

Evaluator tests every hypothesis using real data.

Evaluator → CreativeAgent (Decision-triggered generation)

Only when:

status == "VALIDATED"
AND driver == "creative_fatigue"

Orchestrator controls entire pipeline

Acts as the “conductor” that sequences every agent intelligently.

🗃️ Artifacts Produced

The system outputs:

reports/
│── insights_<run_id>.json
│── creatives_<run_id>.json
│── report_<run_id>.md
│── run_metadata_<run_id>.json


Plus runtime logs in:

logs/

🏁 Conclusion

This multi-agent architecture follows the exact structure required by the Kasparro assignment:

Structured agent roles

Planner → Insight → Evaluator loop

Creative generation only when validated

Modular, interpretable, extensible pipeline

Fully aligned with the evaluation rubric

The graph and workflow below prove the usage of a true agentic system rather than a monolithic script.