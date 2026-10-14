"""Explicit administrator CLI; paths are trusted configuration, never metadata."""
import argparse
from pathlib import Path
from asset_rights_engine import Engine
parser=argparse.ArgumentParser()
parser.add_argument("data_directory",type=Path)
parser.add_argument("--snapshot",help="Unused local .sqlite3 basename")
args=parser.parse_args()
engine=Engine(args.data_directory)
print(engine.validate_database())
if args.snapshot: print(engine.create_database_snapshot(args.snapshot))
