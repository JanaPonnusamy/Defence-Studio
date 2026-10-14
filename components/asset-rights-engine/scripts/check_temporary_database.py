"""Verify the existing administrator CLI entirely in temporary storage."""
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
from asset_rights_engine import Engine

if __name__ == "__main__":
    with TemporaryDirectory(prefix="are-a2-cli-") as directory:
        subprocess.run([sys.executable, str(Path(__file__).with_name("database.py")),
                        directory, "--snapshot", "cli-snapshot.sqlite3"], check=True)
        print("Restored CLI snapshot:", Engine(Path(directory), "cli-snapshot.sqlite3").validate_database())
    print("Temporary CLI storage removed:", not Path(directory).exists())
