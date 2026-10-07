from sqlalchemy import create_engine, text

from scout_api.core.config import get_settings

SID = "5385c7da-c8d7-48c8-a63e-cc897a19d5ef"
engine = create_engine(get_settings().database_url)
with engine.connect() as c:
    logs = c.execute(
        text(
            """
            SELECT sequence, decision, confidence, title, url,
                   store_product_id, reasons
            FROM match_candidate_logs
            WHERE store_run_id = :sid
            ORDER BY sequence
            """
        ),
        {"sid": SID},
    ).mappings().all()
    print("count", len(logs))
    for row in logs:
        print("---")
        print(
            "seq",
            row["sequence"],
            "dec",
            row["decision"],
            "conf",
            row["confidence"],
            "asin",
            row["store_product_id"],
        )
        print("title", (row["title"] or "")[:120])
        print("reasons", row["reasons"])
