import time
from sqlalchemy import create_engine,text
from scout_api.core.config import get_settings
from scout_api.modules.crawler.services.html_fetcher import UrllibHtmlFetcher,is_challenge_page
from scout_api.modules.crawler.spiders.registry import resolve_store_spider
cfg=get_settings()
with create_engine(cfg.database_url).connect() as c:
    url=c.execute(text("select url from store_listings where store='magazineluiza' limit 1")).scalar()
start=time.perf_counter()
try:
    response=UrllibHtmlFetcher(user_agent=cfg.scraper_user_agent).fetch(url)
    print('http_ms',round((time.perf_counter()-start)*1000,2),'challenge',is_challenge_page(response.text),flush=True)
    spider=resolve_store_spider(url)
    offer=spider.extract_offer(response)
    print('offer',offer.model_dump(mode='json'),flush=True)
except Exception as exc:
    print('error',type(exc).__name__,getattr(exc,'code',None),str(exc)[:150],'ms',round((time.perf_counter()-start)*1000,2),flush=True)
