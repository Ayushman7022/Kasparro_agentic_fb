# run.py
import argparse
from src.orchestrator.orchestrator import Orchestrator

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "query",
        type=str,
        help="Analysis query. Example: 'Analyze ROAS drop for campaign_1'"
    )
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    orchestrator = Orchestrator(config_path=args.config)

    try:
        out = orchestrator.run(args.query)
        print("\nCompleted. Artifacts:", out)
    except Exception as e:
        print("\n❌ Pipeline Failed:", str(e))

if __name__ == "__main__":
    main()
