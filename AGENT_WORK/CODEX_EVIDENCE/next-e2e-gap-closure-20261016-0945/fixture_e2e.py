"""Explicit local HTTP transport fixture; real persistent worker and three media services."""
import importlib.util, json, os, sys
from pathlib import Path
repo=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(repo/'backend'))
import httpx
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.acquisition.source_policy import SourcePolicy
from defence_studio.acquisition.handler import build_acquisition_handler
from defence_studio.discovery.handler import build_discovery_handler
from defence_studio.discovery.providers import DiscoveryProviderRegistry, RssFeedProvider
from defence_studio.storage.artifacts import ArtifactStore
spec=importlib.util.spec_from_file_location('acceptance',repo/'backend/scripts/e2e_acceptance.py')
h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
hosts=['fixture-one.example','fixture-two.example']
policy=SourcePolicy.from_dict({'version':'defence-studio.source-policy.v1','sources':[
 {'id':host,'domain':host,'category':'INDEPENDENT_REPORTING','publisher':host,'independence_group':host,'enabled':True,'url_patterns':['^/']} for host in hosts]})
guard=UrlGuard(resolver=lambda host,port:['93.184.216.34'],policy=policy)
text=('Officials said the Training Command practiced readiness procedures. '
      'Officials said the Training Command practiced routine safety checks. '
      'Officials said the Training Command practiced communication procedures. '
      'Officials said the Training Command practiced planning procedures. ')
def transport(req):
    assert req.url.host in hosts, 'unexpected fixture network request'
    if req.url.path=='/feed':
        body=f'<rss><channel><title>Local fixture</title><item><title>Training exercise readiness briefing</title><link>https://{req.url.host}/article</link><description>{text}</description><pubDate>Fri, 16 Oct 2026 03:00:00 GMT</pubDate></item></channel></rss>'
        return httpx.Response(200,headers={'content-type':'application/rss+xml'},text=body)
    return httpx.Response(200,headers={'content-type':'text/html'},text=f'<html><head><title>Training exercise readiness briefing</title></head><body><article><h1>Training exercise briefing</h1><p>{text}</p><p>{text}</p></article></body></html>')
factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(transport),**kw)
registry=DiscoveryProviderRegistry(); registry.register(RssFeedProvider(feeds_by_language={'en':tuple(f'https://{x}/feed' for x in hosts)},client_factory=factory))
original=h.build_research_worker
def worker(db,root):
    store=ArtifactStore(root)
    return original(db,root,discovery_handler=build_discovery_handler(store,registry=registry,guard=guard,client_factory=factory),acquisition_handler=build_acquisition_handler(store,guard=guard,client_factory=factory))
h.build_research_worker=worker
h.REPORT['input_mode']='LOCAL_HTTP_TRANSPORT_FIXTURE'
h.REPORT['simulated'].append('RSS and article HTTP transport: controlled local text, never live news')
os.environ['E2E_DIR']='C:/dstmp/next-fixture-0945'
out=Path(__file__).with_name('fixture-e2e.json')
sys.argv=[str(repo/'backend/scripts/e2e_acceptance.py'),'Training','--out',str(out)]
code=h.main()
report=json.loads(out.read_text(encoding='utf-8'))
report['acceptance_status']='FIXTURE_E2E_VERIFIED' if code==0 else 'BLOCKED'
for step in report['steps']:
    step['step']=step['step'].replace('real RSS feeds','local fixture RSS').replace('real article fetch','local fixture article transport')
out.write_text(json.dumps(report,indent=2),encoding='utf-8')
raise SystemExit(code)
