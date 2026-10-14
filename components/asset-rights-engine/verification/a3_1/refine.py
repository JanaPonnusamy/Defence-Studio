from pathlib import Path
p=Path('src/asset_rights_engine/engine.py')
s=p.read_text().replace('from contextlib import contextmanager','from contextlib import contextmanager, closing').replace('with sqlite3.connect(":memory:") as expected:','with closing(sqlite3.connect(":memory:")) as expected:').replace('                require(digest(payload)==row["digest"],','                self._stored(context,lambda: revision(row["revision"]))\n                require(self._stored(context,lambda: digest(payload))==row["digest"],')
p.write_text(s)
