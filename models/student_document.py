from sqlalchemy import Column, ForeignKey, Integer, String
from database.database import Base


class StudentDocument(Base):
    __tablename__ = 'student_documents'
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    document_type = Column(String(100), nullable=False)
    path = Column(String(500), nullable=False)
    media_type = Column(String(100), nullable=False)
