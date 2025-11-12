from sqlalchemy import Column, Integer, String, Date, DateTime, func
from sqlalchemy.orm import declarative_base
from db import engine

Base = declarative_base()

class License(Base):
    __tablename__ = "licenses"
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    state = Column(String, nullable=False)
    license_number = Column(String, nullable=False)
    status = Column(String)
    issue_date = Column(Date)
    expiry_date = Column(Date)
    source_uri = Column(String)
    last_verified_at = Column(DateTime, server_default=func.now())

    def as_dict(self):
        return {
            "id": self.id,
            "full_name": self.full_name,
            "state": self.state,
            "license_number": self.license_number,
            "status": self.status,
            "issue_date": str(self.issue_date) if self.issue_date else None,
            "expiry_date": str(self.expiry_date) if self.expiry_date else None,
            "source_uri": self.source_uri,
            "last_verified_at": str(self.last_verified_at) if self.last_verified_at else None,
        }

def create_all():
    Base.metadata.create_all(bind=engine)
