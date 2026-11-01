from sqlalchemy import BigInteger, Column, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import foreign, relationship

from database.database import Base


class FeeAssignment(Base):
    __tablename__ = 'fee_assignments'

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey('schools.id', ondelete='CASCADE'), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey('sessions.id', ondelete='RESTRICT'), nullable=False, index=True)
    class_id = Column(Integer, ForeignKey('classes.id', ondelete='RESTRICT'), nullable=False, index=True)
    section_id = Column(Integer, ForeignKey('sections.id', ondelete='RESTRICT'), nullable=True, index=True)
    fee_configure_id = Column(
        BigInteger,
        ForeignKey('fee_configuration_heads.id', ondelete='RESTRICT', name='fk_fee_assignments_fee_configure_id'),
        nullable=False,
        index=True,
    )
    school_class = relationship('SchoolClass')
    section = relationship('Section')
    fee_configuration = relationship(
        'FeeConfigurationHead',
        primaryjoin='foreign(FeeAssignment.fee_configure_id) == FeeConfigurationHead.id',
        viewonly=True,
        uselist=False,
    )
    __table_args__ = (
        UniqueConstraint('school_id', 'session_id', 'class_id', 'section_id', name='uq_fee_assignment_class_section'),
    )

    @property
    def class_name(self):
        return self.school_class.name

    @property
    def fee_structure_name(self):
        configuration = self.fee_configuration
        return configuration.fee_structure.fee_structure_name if configuration is not None else None

    @property
    def fee_structure_id(self):
        configuration = self.fee_configuration
        return configuration.fee_structure_id if configuration is not None else None

    @property
    def section_name(self):
        return self.section.name if self.section is not None else None
