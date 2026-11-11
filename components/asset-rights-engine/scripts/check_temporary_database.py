"""Verify the existing administrator CLI entirely in temporary storage."""
import sys
from pathlib import Path
import subprocess

_REPO = None
for _parent in Path(__file__).resolve().parents:
    if (_parent / "tools" / "project_paths.py").is_file():
        _REPO = _parent
        break
if _REPO is None:
    raise RuntimeError("Defence Era root not found; refusing an uncontrolled temporary directory")
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from tools.project_paths import temporary_directory
from asset_rights_engine import Engine

if __name__ == "__main__":
    with temporary_directory("are-a2-cli") as directory:
        subprocess.run([sys.executable, str(Path(__file__).with_name("database.py")),
                        directory, "--snapshot", "cli-snapshot.sqlite3"], check=True)
        print("Restored CLI snapshot:", Engine(Path(directory), "cli-snapshot.sqlite3").validate_database())
    print("Temporary CLI storage removed:", not Path(directory).exists())
