from database.advisory_lock import acquire_transaction_lock
from sqlalchemy import text

from models.class_section import SchoolClass, Section, ClassSectionMapping


def ensure_class_section_tables(connection):
    if connection.dialect.name == "postgresql":
        acquire_transaction_lock(connection, 731904220)
    for model in (SchoolClass, Section, ClassSectionMapping):
        model.__table__.create(bind=connection, checkfirst=True)
