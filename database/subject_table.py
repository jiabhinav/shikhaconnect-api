from database.advisory_lock import acquire_transaction_lock
from sqlalchemy import text

from models.subject import Subject


def ensure_subject_table(connection):
    if connection.dialect.name == "postgresql":
        acquire_transaction_lock(connection, 731904221)
    Subject.__table__.create(bind=connection, checkfirst=True)
