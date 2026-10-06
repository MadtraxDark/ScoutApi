"""Look up motherboard products that could exercise Terabyte Match."""

from __future__ import annotations

from scout_api.core.db import get_session_factory
from sqlalchemy import text


def main() -> None:
    sf = get_session_factory()
    with sf() as session:
        rows = (
            session.execute(
                text(
                    """
                SELECT id, title, brand, model
                FROM products
                WHERE lower(coalesce(title, '')) LIKE '%b650m%aorus%elite%'
                   OR lower(coalesce(model, '')) LIKE '%b650m%aorus%elite%'
                ORDER BY id DESC
                LIMIT 10
                """
                )
            )
            .mappings()
            .all()
        )
        print("rows", len(rows))
        for row in rows:
            print(dict(row))


if __name__ == "__main__":
    main()
