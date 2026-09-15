from sqlalchemy import Column, Enum, ForeignKey, Integer, UniqueConstraint
from enum import Enum as PyEnum

from database.database import Base


class SchoolMappingStatus(str, PyEnum):
    ACTIVE = "active"
    DEACTIVE = "deactive"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            return {"active": cls.ACTIVE, "deactive": cls.DEACTIVE,
                    "inactive": cls.DEACTIVE}.get(value.strip().lower())
        return None


class SchoolMapping(Base):
    __tablename__ = "school_mapping"
    __table_args__ = (UniqueConstraint("school_id", "user_id", name="uq_school_mapping_user"),)

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(Enum(SchoolMappingStatus, native_enum=False, create_constraint=True,
                         values_callable=lambda enum: [item.value for item in enum],
                         name="school_mapping_status"), nullable=False)
