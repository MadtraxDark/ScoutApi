import json
import logging
import time
from scout_api.modules.matching.identity import identity_reference_item
from scout_api.modules.matching.product_match_service import ProductMatchService

logging.basicConfig(level=logging.INFO)
ref=identity_reference_item('Celular Samsung Galaxy S25 Ultra 5G 256GB Titânio Preto',brand='Samsung',category='smartphone')
started=time.perf_counter()
result=ProductMatchService().match_from_item(ref,stores=['amazon_br','amazon_us'],persist=False,include_review=True,max_candidates_per_store=3,clear_reference_price=True)
print('REPORT='+json.dumps({'ms':round((time.perf_counter()-started)*1000,2),'matches':[{'store':h.store,'decision':h.decision,'price':str(h.product.price) if h.product else None,'title':h.product.title if h.product else None,'asin':h.product.product_id if h.product else None,'reasons':[r.code for r in h.reasons]} for h in result.matches],'errors':result.model_dump(mode='json').get('errors',[])}),flush=True)
