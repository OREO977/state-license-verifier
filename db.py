import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# If DATABASE_URL is set (e.g., Render Postgres), we use that. Otherwise we use a local SQLite file.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////tmp/state_license.db")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

def init_db():
    # Placeholder for future init work if needed
    return
