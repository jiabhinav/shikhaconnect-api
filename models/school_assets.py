from sqlalchemy import Column, ForeignKey, Integer, String

from database.database import Base


class SchoolAssets(Base):
    __tablename__ = "school_assets"

    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), primary_key=True)
    school_logo = Column(String(500), nullable=True)
    board_logo = Column(String(500), nullable=True)
    principal_signature = Column(String(500), nullable=True)
    exam_coordinator_signature = Column(String(500), nullable=True)
