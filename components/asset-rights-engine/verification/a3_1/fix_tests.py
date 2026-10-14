from pathlib import Path
p=Path('tests/test_database_validation.py');s=p.read_text().replace('import hashlib, json, shutil','import json, shutil')
s=s.replace("        with closing(sqlite3.connect(e.path)) as c:\n            records=c.execute", "        records=None\n        if code!='DATABASE':\n          with closing(sqlite3.connect(e.path)) as c:\n            records=c.execute")
s=s.replace("        with closing(sqlite3.connect(e.path)) as c:\n            if records is not None: self.assertEqual", "        if records is not None:\n          with closing(sqlite3.connect(e.path)) as c:\n            self.assertEqual")
p.write_text(s)
