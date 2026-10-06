import inspect
import json
import os
from pathlib import Path
from importlib.metadata import version
from urllib.request import urlopen
from sqlalchemy import create_engine,text
from scout_api.core.config import get_settings
from scout_api.modules.crawler.services.amazon_http_first_fetcher import AmazonHttpFirstHtmlFetcher
cfg=get_settings()
print('health:',json.load(urlopen('http://127.0.0.1:8000/health',timeout=5)),flush=True)
print('camoufox:',version('camoufox'),'capacity:',cfg.camoufox_browser_capacity,'scheduler:',cfg.camoufox_browser_scheduler_enabled,'auth_required:',cfg.auth_required,flush=True)
print('fpgen_app_writable:',os.access('/usr/local/lib/python3.12/site-packages/fpgen/data',os.R_OK|os.W_OK),flush=True)
print('browser_versions:',[p.parent.name for p in Path('/home/app/.cache/camoufox/browsers').rglob('version.json')],flush=True)
with create_engine(cfg.database_url).connect() as c:
    print('schema:',c.execute(text('select version_num from alembic_version')).scalar(),flush=True)
    print('runs:',c.execute(text('select status,count(*) from product_match_runs group by status')).all(),flush=True)
    print('catalog:',c.execute(text('select count(*) from canonical_products')).scalar(),'images:',c.execute(text('select count(*) from product_images')).scalar(),flush=True)
print('amazon_fallback_installed:', 'amazon_http_empty_buybox_fallback_browser' in inspect.getsource(AmazonHttpFirstHtmlFetcher.fetch),flush=True)
