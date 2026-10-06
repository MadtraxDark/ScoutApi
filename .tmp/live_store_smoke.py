import json
import logging
import os
import time
from functools import wraps
from sqlalchemy import create_engine, text
from scout_api.core.config import get_settings
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import ProductScrapeService, get_shared_html_fetcher
from scout_api.modules.crawler.services.html_fetcher import CamoufoxHtmlFetcher
from scout_api.modules.matching.store_search_service import StoreSearchService
from scout_api.modules.matching.identity import identity_reference_item, identity_from_price_item, build_search_queries
from scout_api.modules.matching.engine import MatchingEngine

logging.basicConfig(level=logging.INFO)
stages={}
for name in ('_acquire_browser','_maybe_warmup','_goto_with_bestbuy_retry','_wait_for_product_html'):
    original=getattr(CamoufoxHtmlFetcher,name)
    def wrap(original,name):
        @wraps(original)
        def timed(*args,**kwargs):
            started=time.perf_counter()
            try:return original(*args,**kwargs)
            finally:
                ms=round((time.perf_counter()-started)*1000,2)
                stages[name]=stages.get(name,0)+ms
                print(json.dumps({'stage':name,'ms':ms}),flush=True)
        return timed
    setattr(CamoufoxHtmlFetcher,name,wrap(original,name))

fetcher=get_shared_html_fetcher()
guard=ScrapeGuard(url_cooldown_seconds=0,domain_min_interval_seconds=1,result_cache_ttl_seconds=600)
scrape=ProductScrapeService(fetcher=fetcher,guard=guard)
search=StoreSearchService(fetcher=fetcher)
ref=identity_reference_item('Celular Samsung Galaxy S25 Ultra 5G 256GB Titanio Preto',brand='Samsung',category='smartphone')
identity=identity_from_price_item(ref)
report=[]
for store,locale in ([] if os.environ.get('MAGALU_ONLY') else [('amazon_br','pt-BR'),('amazon_us','en-US')]):
    queries=build_search_queries(identity,locale=locale)
    for query in queries[:2]:
        started=time.perf_counter()
        print(json.dumps({'begin':'search','store':store,'query':query}),flush=True)
        try:
            candidates=search.search(store,query,limit=5)
            row={'store':store,'query':query,'count':len(candidates),'ms':round((time.perf_counter()-started)*1000,2),'candidates':[{'title':c.title,'url':c.url} for c in candidates]}
        except Exception as exc:
            candidates=[]
            row={'store':store,'query':query,'error':getattr(exc,'code',type(exc).__name__),'ms':round((time.perf_counter()-started)*1000,2)}
        print(json.dumps(row),flush=True)
        report.append(row)
        if candidates:break
    url='https://www.amazon.com.br/dp/B0DSYJCY45' if store=='amazon_br' else (candidates[0].url if candidates else 'https://www.amazon.com/dp/B0DSYVPHP4')
    started=time.perf_counter()
    try:
        item=scrape.scrape(url,include_images=False)
        score=MatchingEngine().score(identity,identity_from_price_item(item))
        row={'store':store,'pdp':url,'title':item.title,'price':str(item.price),'available':item.available,'decision':score.decision,'reasons':[r.code for r in score.reasons],'ms':round((time.perf_counter()-started)*1000,2)}
    except Exception as exc:
        row={'store':store,'pdp':url,'error':getattr(exc,'code',type(exc).__name__),'ms':round((time.perf_counter()-started)*1000,2)}
    print(json.dumps(row),flush=True)
    report.append(row)

with create_engine(get_settings().database_url).connect() as c:
    magalu=c.execute(text("select url from store_listings where store='magazineluiza' limit 1")).scalar()
if magalu:
    for trial in range(3):
        # Independent fetch contexts produce cold samples without changing navigation.
        get_shared_html_fetcher.cache_clear()
        stages.clear()
        service=ProductScrapeService(guard=guard)
        started=time.perf_counter()
        try:
            if trial:guard=ScrapeGuard(url_cooldown_seconds=0,domain_min_interval_seconds=1,result_cache_ttl_seconds=600);service=ProductScrapeService(guard=guard)
            item=service.scrape(magalu,include_images=True)
            cold_ms=round((time.perf_counter()-started)*1000,2)
            cached=time.perf_counter();service.scrape(magalu,include_images=True)
            row={'store':'magazineluiza','trial':trial,'ms':cold_ms,'cache_ms':round((time.perf_counter()-cached)*1000,2),'images':len(item.images),'stages':dict(stages),'title':item.title,'price':str(item.price)}
        except Exception as exc:
            row={'store':'magazineluiza','trial':trial,'error':getattr(exc,'code',type(exc).__name__),'ms':round((time.perf_counter()-started)*1000,2),'stages':dict(stages)}
        print(json.dumps(row),flush=True)
        report.append(row)
        # Bound the sampling frequency; no production job or persisted offer is created.
        time.sleep(15)
print('REPORT='+json.dumps(report),flush=True)
