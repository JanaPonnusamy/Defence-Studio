"""Read-only numbered artifact inventory for ARE-A3 evidence."""
from pathlib import Path
NAMES = [
 "src/asset_rights_engine/engine.py", "src/asset_rights_engine/contracts.py",
 "src/asset_rights_engine/evaluator.py", "src/asset_rights_engine/adapter_contracts.py",
 "src/asset_rights_engine/host_adapter.py", "src/asset_rights_engine/simulator.py",
 "src/asset_rights_engine/__init__.py", "src/asset_rights_engine/schema.sql",
 "migrations/001_initial.sql", "tests/test_engine.py", "tests/test_host_adapter.py",
 "tests/test_adapter_simulator.py", "scripts/database.py", "scripts/verify_release.py",
 "scripts/test_offline.py", "scripts/check_temporary_database.py", "scripts/example.py",
 "scripts/adapter_simulator.py", "pyproject.toml", "README.md", "CONTRACTS.md",
 "ARCHITECTURE.md", "SECURITY.md", "CHANGELOG.md", "docs/HOST_ADAPTER.md",
 "ACCEPTANCE_REPORT.md", "A2_ACCEPTANCE_REPORT.md", "A2_BASELINE.json", ".gitignore"
]
for name in NAMES:
    raw=Path(name).read_bytes()
    try:
        text=raw.decode("utf-8"); encoding="utf-8"
    except UnicodeDecodeError:
        text=raw.decode("cp1252"); encoding="cp1252 fallback (original bytes unchanged)"
    print("FILE", name, "ENCODING", encoding)
    for line_number, line in enumerate(text.splitlines(),1):
        print(f"{line_number:4}: {line}")
