"""OFFLINE TEST SIMULATOR: no authenticated identities or real rights evidence."""
from dataclasses import asdict
import json
from asset_rights_engine.simulator import run_simulation

if __name__ == "__main__":
    print(json.dumps(asdict(run_simulation()), indent=2))
