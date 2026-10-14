from pathlib import Path
import sys,json
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'tests'))
from test_engine import asset,rights,binding,usage,request
from asset_rights_engine import Engine,__version__
e=Engine(Path(sys.argv[1]));e.register_asset(asset());r=e.create_rights_revision('asset-1',rights());e.review_rights('asset-1',request(r));a=e.create_assignment('assignment-1','asset-1',binding(),'background',usage(),'PENDING_REVIEW');e.review_assignment('assignment-1',request(a,'assignment-review'),binding(),usage());print(json.dumps({'version':__version__,'validation':e.validate_database()}))
