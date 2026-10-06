from scout_api.core.config import get_settings
from sqlalchemy import create_engine, text, inspect
from scout_api.modules.images.drive_client import GoogleDriveClient
e=create_engine(get_settings().database_url)
with e.connect() as c:
    print('schema:',c.execute(text('select version_num from alembic_version')).scalar(),flush=True)
    print('runs:',c.execute(text('select status,count(*) from product_match_runs group by status')).all(),flush=True)
    names=inspect(e).get_table_names()
    print('listing tables:',[n for n in names if 'listing' in n or 'image' in n],flush=True)
    for name in names:
        if 'listing' in name:
            columns=[col['name'] for col in inspect(e).get_columns(name)]
            print(name,columns,flush=True)
            if 'store' in columns and 'url' in columns:
                print('magalu:',c.execute(text(f"select url from {name} where store='magazineluiza' limit 1")).scalar(),flush=True)
d=GoogleDriveClient()
print('Drive conectado:',bool(d.ensure_folder('products',parent_id=d.root_folder_id)),flush=True)
