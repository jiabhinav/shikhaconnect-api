"""Add session ownership to existing school catalog records."""
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from utils.dates import today

CATALOG_TABLES = ('caste_categories', 'classes', 'sections', 'fee_categories',
                  'houses', 'streams', 'subjects')


def migrate_session_catalogs(connection):
    from database.database import Base

    inspector = inspect(connection)
    pending = [name for name in CATALOG_TABLES if inspector.has_table(name)
               and 'session_id' not in {c['name'] for c in inspector.get_columns(name)}]
    if not pending:
        return
    if connection.dialect.name not in ('postgresql', 'sqlite'):
        raise SQLAlchemyError('Session catalog migration requires PostgreSQL or SQLite')
    if connection.dialect.name == 'postgresql':
        connection.execute(text('SELECT pg_advisory_xact_lock(731904218)'))
    for name in pending:
        if connection.dialect.name == 'postgresql':
            connection.execute(text(f'LOCK TABLE {name} IN ACCESS EXCLUSIVE MODE'))
        if 'session_id' in {c['name'] for c in inspect(connection).get_columns(name)}:
            continue
        connection.execute(text(f'''
            ALTER TABLE {name} ADD COLUMN session_id INTEGER
            REFERENCES sessions(id) ON DELETE RESTRICT
        '''))
        # Keep IDs and dependent references intact. When sessions overlap, use
        # the latest start date, then the latest ID, for a deterministic choice.
        connection.execute(text(f'''
            UPDATE {name} SET session_id=(
                SELECT s.id FROM sessions s WHERE s.school_id={name}.school_id
                AND s.start_date <= :today AND s.end_date >= :today
                ORDER BY s.start_date DESC, s.id DESC LIMIT 1
            )
        '''), {'today': today()})
        table = Base.metadata.tables[name]
        for index in table.indexes:
            if index.unique:
                index.drop(connection, checkfirst=True)
                index.create(connection)
            elif any(column.name == 'session_id' for column in index.columns):
                index.create(connection, checkfirst=True)
