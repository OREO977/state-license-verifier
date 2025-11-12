from sqlalchemy import select
from sqlalchemy.orm import Session
from db import SessionLocal
from models import License
from ut_adapter import verify_ut

def run_ut_job(providers: list[str]):
    """
    For each provider name:
      - call Utah adapter
      - upsert into the licenses table (by state + license_number)
    """
    session: Session = SessionLocal()
    summary = {"processed": 0}
    try:
        for name in providers:
            results = verify_ut(name)  # list of dicts
            for rec in results:
                existing = session.execute(
                    select(License).where(
                        License.state == rec["state"],
                        License.license_number == rec["license_number"],
                    )
                ).scalar_one_or_none()

                if existing:
                    # update fields
                    existing.full_name = rec["full_name"]
                    existing.status = rec.get("status")
                    existing.issue_date = rec.get("issue_date")
                    existing.expiry_date = rec.get("expiry_date")
                    existing.source_uri = rec.get("source_uri")
                    session.add(existing)
                else:
                    session.add(License(**rec))

                session.commit()
                summary["processed"] += 1
        return summary
    finally:
        session.close()
