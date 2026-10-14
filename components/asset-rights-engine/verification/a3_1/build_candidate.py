from pathlib import Path
import shutil,tempfile,subprocess
root=Path.cwd();out=root/'verification/a3_1/candidate';out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=root/'verification/a3_1/tmp') as directory:
 stage=Path(directory);shutil.copy2(root/'pyproject.toml',stage/'pyproject.toml')
 shutil.copytree(root/'src/asset_rights_engine',stage/'src/asset_rights_engine',ignore=shutil.ignore_patterns('__pycache__'))
 subprocess.run(['python','-B','-m','pip','wheel',str(stage),'--no-index','--no-deps','--no-build-isolation','--wheel-dir',str(out)],check=True)
