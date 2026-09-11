from sqlalchemy import Column, ForeignKey, Index, Integer, String, func

from database.database import Base


class FeeCategory(Base):
    __tablename__ = "fee_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    __table_args__ = (
        Index("uq_fee_category_school_name", school_id, func.lower(func.trim(name)), unique=True),
    )
