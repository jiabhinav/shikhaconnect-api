"""Add session ownership to existing school catalog records."""
from sqlalchemy import column, inspect, select, table, text, update
from sqlalchemy.exc import SQLAlchemyError
from utils.dates import today
from utils.session_status import SessionStatus

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
        catalog = table(name, column('school_id'), column('session_id'))
        sessions = table('sessions', column('id'), column('school_id'),
                         column('start_date'), column('end_date'))
        current_session = select(sessions.c.id).where(
            sessions.c.school_id == catalog.c.school_id,
            SessionStatus.is_current(sessions.c.start_date, sessions.c.end_date, as_of=today()),
        ).order_by(sessions.c.start_date.desc(), sessions.c.id.desc()).limit(1).correlate(catalog)
        connection.execute(update(catalog).values(session_id=current_session.scalar_subquery()))
        catalog_table = Base.metadata.tables[name]
        for index in catalog_table.indexes:
            if index.unique:
                index.drop(connection, checkfirst=True)
                index.create(connection)
            elif any(column.name == 'session_id' for column in index.columns):
                index.create(connection, checkfirst=True)
