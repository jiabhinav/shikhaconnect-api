from sqlalchemy import Column, ForeignKey, Index, Integer, String, func

from database.database import Base


class CasteCategory(Base):
    __tablename__ = "caste_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    __table_args__ = (
        Index("uq_caste_category_school_name", school_id, func.lower(func.trim(name)), unique=True),
    )
