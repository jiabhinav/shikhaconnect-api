from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship

from database.database import Base


class FeeAssignment(Base):
    __tablename__ = 'fee_assignments'

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey('schools.id', ondelete='CASCADE'), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey('sessions.id', ondelete='RESTRICT'), nullable=False, index=True)
    class_id = Column(Integer, ForeignKey('classes.id', ondelete='RESTRICT'), nullable=False, index=True)
    fee_structure_id = Column(Integer, ForeignKey('fee_structures.id', ondelete='RESTRICT'), nullable=False, index=True)
    school_class = relationship('SchoolClass')
    fee_structure = relationship('FeeStructure')
    __table_args__ = (
        UniqueConstraint('school_id', 'session_id', 'class_id', name='uq_fee_assignment_class'),
    )

    @property
    def class_name(self):
        return self.school_class.name

    @property
    def fee_structure_name(self):
        return self.fee_structure.fee_structure_name
