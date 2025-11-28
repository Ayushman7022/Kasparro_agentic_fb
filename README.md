Overview

This project implements a multi-agent FB Ads Intelligence System that analyzes ad performance, validates hypotheses using real data, and generates new creatives when needed.
The system is built for the Kasparro Agentic Hackathon using a modular and extensible architecture.

🧠 System Design (High-Level)----------------
Architecture Flow
User Query
     ↓
Planner Agent → creates structured tasks
     ↓
Insight Agent → generates hypotheses for each task
     ↓
Evaluator Agent → validates hypotheses with statistical tests
     ↓
Creative Agent → generates new creatives (only when fatigue validated)
     ↓
Orchestrator → compiles final insights, creatives & report

Agents-------------
Planner Agent – Breaks down the query into actionable tasks.

Insight Agent – Produces hypotheses based on tasks + data summary.

Evaluator Agent – Validates hypotheses using t-tests, bootstrap, effect size, and change-point detection.

Creative Agent – Generates fresh, deduplicated ad creatives for validated "creative_fatigue" hypotheses.

Data Agent – Loads data, provides summaries and timeseries.

Orchestrator – Controls the entire multi-agent pipeline and generates final outputs.

📂 Project Structure-----------
root/
│── run.py
│── README.md
│── agent_graph.md
│── config/
│── prompts/
│── data/
│── logs/
│── reports/
└── src/
    ├── agents/
    ├── orchestrator/
    └── utils/

▶️ How to Run-------------
python run.py "Analyze CTR drop and creative fatigue"


Output files will be saved to the reports/ folder:---------------
insights_<run_id>.json

creatives_<run_id>.json

report_<run_id>.md

run_metadata_<run_id>.json

Logs are saved in logs/.

🛠️ Tech Stack-------------
Python 3.10+

Gemini 2.0 Flash (via google-generativeai)

Pandas, NumPy, SciPy

Pydantic

Mermaid diagrams (for agent_graph.md)

✔️ Features---------------
Multi-agent architecture

JSON-consistent LLM prompting

Robust statistical evaluation

Automated creative generation

Rich markdown reporting

Full logging + run metadata
