import os

BOT_NAME = "scout_api_crawler"
SPIDER_MODULES = ["scout_api.modules.crawler.spiders"]
NEWSPIDER_MODULE = "scout_api.modules.crawler.spiders"
ROBOTSTXT_OBEY = True
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 2
AUTOTHROTTLE_MAX_DELAY = 60
AUTOTHROTTLE_TARGET_CONCURRENCY = 0.5
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_DELAY = 2
RETRY_TIMES = 3
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504]
DOWNLOADER_MIDDLEWARES = {
    "scout_api.modules.crawler.middlewares.PoliteRetryMiddleware": 550
}
USER_AGENT = os.getenv("SCRAPER_USER_AGENT", "ScoutApiV2/0.1 (+price-monitoring)")
ITEM_PIPELINES = {"scout_api.modules.crawler.pipelines.PriceHistoryPipeline": 300}
