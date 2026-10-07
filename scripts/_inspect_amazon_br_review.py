"""Inspect latest amazon_br match store outcomes + candidate reasons."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from scout_api.core.config import get_settings


def main() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  msr.id,
                  msr.store,
                  msr.status,
                  msr.matched_decision,
                  msr.error_code,
                  msr.error_message,
                  msr.matched_title,
                  msr.matched_price,
                  msr.matched_confidence,
                  msr.matched_url,
                  msr.finished_at,
                  pmr.id AS run_id,
                  cp.title AS product_title
                FROM match_store_runs msr
                JOIN product_match_runs pmr ON pmr.id = msr.run_id
                LEFT JOIN canonical_products cp ON cp.id = pmr.product_id
                WHERE msr.store = 'amazon_br'
                ORDER BY msr.finished_at DESC NULLS LAST
                LIMIT 8
                """
            )
        ).mappings().all()
        for row in rows:
            print("--- store_run ---")
            for key, value in dict(row).items():
                if value is not None:
                    text_val = str(value)
                    print(f"{key}: {text_val[:180]}")
            logs = conn.execute(
                text(
                    """
                    SELECT decision, confidence, title, url, reasons_json
                    FROM match_candidate_logs
                    WHERE store_run_id = :sid
                    ORDER BY created_at DESC
                    LIMIT 5
                    """
                ),
                {"sid": row["id"]},
            ).mappings().all()
            for log in logs:
                print("  candidate:", dict(log))


if __name__ == "__main__":
    main()
