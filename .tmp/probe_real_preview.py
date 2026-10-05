import time
from uuid import UUID
from unittest.mock import patch
from sqlalchemy import select, func
from scout_api.core.database import get_session_factory
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.models import CanonicalProduct
from scout_api.modules.images.models import ProductImage
from scout_api.modules.images.pipeline import ImagePipeline
from scout_api.modules.images.drive_client import GoogleDriveClient
from scout_api.modules.matching.product_registration_service import ProductRegistrationService
from scout_api.modules.crawler.services.product_scrape_service import ProductScrapeService

def counts():
    with get_session_factory()() as s:
        return [s.scalar(select(func.count()).select_from(m)) for m in (CanonicalProduct, ProductImage)]
with get_session_factory()() as s:
    row=MatchingRepository(s).get_canonical(UUID('37fe4214-752e-4e32-b615-e482535d7a02'))
    url=next(x.canonical_url for x in row.listings if x.store=='magazineluiza')
before=counts()
t=time.perf_counter()
with patch.object(ImagePipeline,'register_references',side_effect=AssertionError('preview wrote catalog')), patch.object(ImagePipeline,'persist_approved',side_effect=AssertionError('preview persisted image')), patch.object(GoogleDriveClient,'upload_bytes',side_effect=AssertionError('preview uploaded Drive')), patch.object(ProductRegistrationService,'register_saved',side_effect=AssertionError('preview imported product')):
    item=ProductScrapeService().scrape(url,include_images=True)
print({'preview_ms':round((time.perf_counter()-t)*1000,1),'images':len(item.images),'catalog_unchanged':counts()==before},flush=True)
