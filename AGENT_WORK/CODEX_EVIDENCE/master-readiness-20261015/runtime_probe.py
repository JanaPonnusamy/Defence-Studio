"""Import/start the documented application using only declared runtime dependencies."""
import os
from pathlib import Path
import tempfile

with tempfile.TemporaryDirectory(prefix='ds-runtime-probe-') as directory:
    os.environ['DEFENCE_STUDIO_AUTH_SECRET']='disposable-runtime-probe-secret-at-least-32-bytes'
    os.environ['DEFENCE_STUDIO_DB_PATH']=str(Path(directory)/'test.sqlite3')
    os.environ['DEFENCE_STUDIO_STORAGE_PATH']=str(Path(directory)/'storage')
    from defence_studio.api.app import app
    from defence_studio.services.voice_engine import catalog
    print('app:',app.title)
    print('catalog entries:',len(catalog()))
