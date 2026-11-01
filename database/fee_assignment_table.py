"""Fee assignment schema migrations."""
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.schema import CreateTable


def remove_fee_assignment_admission_amount(connection):
    if not inspect(connection).has_table('fee_assignments'):
        return
    if 'admission_amount' not in {col['name'] for col in inspect(connection).get_columns('fee_assignments')}:
        return
    if connection.dialect.name == 'postgresql':
        connection.execute(text('SELECT pg_advisory_xact_lock(731904218)'))
        if 'admission_amount' not in {col['name'] for col in inspect(connection).get_columns('fee_assignments')}:
            return
        connection.execute(text('ALTER TABLE fee_assignments DROP COLUMN admission_amount'))
    elif connection.dialect.name == 'sqlite':
        # SQLite cannot drop a column referenced by a table CHECK constraint.
        from database.database import Base
        from models.fee_assignment import FeeAssignment
        metadata = MetaData()
        for table in Base.metadata.sorted_tables:
            table.to_metadata(metadata)
        replacement = FeeAssignment.__table__.to_metadata(metadata, name='fee_assignments_rebuilt')
        connection.execute(CreateTable(replacement))
        columns = ', '.join(column.name for column in FeeAssignment.__table__.columns)
        connection.execute(text(f'INSERT INTO fee_assignments_rebuilt ({columns}) SELECT {columns} FROM fee_assignments'))
        connection.execute(text('DROP TABLE fee_assignments'))
        connection.execute(text('ALTER TABLE fee_assignments_rebuilt RENAME TO fee_assignments'))
        for index in FeeAssignment.__table__.indexes:
            index.create(connection)
    else:
        connection.execute(text('ALTER TABLE fee_assignments DROP COLUMN admission_amount'))


def ensure_fee_assignment_section_column(connection):
    """Add an optional section reference to existing fee assignments."""
    inspector = inspect(connection)
    if not inspector.has_table('fee_assignments'):
        return
    if 'section_id' in {col['name'] for col in inspector.get_columns('fee_assignments')}:
        return
    connection.execute(text(
        'ALTER TABLE fee_assignments ADD COLUMN section_id INTEGER '
        'REFERENCES sections(id) ON DELETE RESTRICT'
    ))
    connection.execute(text(
        'CREATE INDEX IF NOT EXISTS ix_fee_assignments_section_id '
        'ON fee_assignments (section_id)'
    ))


def rename_fee_assignment_structure_column(connection):
    """Rename the persisted fee structure reference without losing assignments."""
    inspector = inspect(connection)
    if not inspector.has_table('fee_assignments'):
        return
    columns = {column['name'] for column in inspector.get_columns('fee_assignments')}
    if 'fee_structure_id' in columns and 'fee_configure_id' not in columns:
        connection.execute(text(
            'ALTER TABLE fee_assignments RENAME COLUMN fee_structure_id TO fee_configure_id'
        ))


def migrate_fee_assignment_section_uniqueness(connection):
    """Allow one assignment per class section instead of one per class."""
    inspector = inspect(connection)
    if not inspector.has_table('fee_assignments'):
        return
    unique_constraints = inspector.get_unique_constraints('fee_assignments')
    if any(c.get('name') == 'uq_fee_assignment_class_section' for c in unique_constraints):
        return
    if connection.dialect.name == 'postgresql':
        connection.execute(text('ALTER TABLE fee_assignments DROP CONSTRAINT IF EXISTS uq_fee_assignment_class'))
        connection.execute(text(
            'ALTER TABLE fee_assignments ADD CONSTRAINT uq_fee_assignment_class_section '
            'UNIQUE (school_id, session_id, class_id, section_id)'
        ))
        return
    if connection.dialect.name == 'sqlite':
        from models.fee_assignment import FeeAssignment
        metadata = MetaData()
        replacement = FeeAssignment.__table__.to_metadata(metadata, name='fee_assignments_rebuilt')
        connection.execute(CreateTable(replacement))
        columns = ', '.join(column.name for column in FeeAssignment.__table__.columns)
        connection.execute(text(f'INSERT INTO fee_assignments_rebuilt ({columns}) SELECT {columns} FROM fee_assignments'))
        connection.execute(text('DROP TABLE fee_assignments'))
        connection.execute(text('ALTER TABLE fee_assignments_rebuilt RENAME TO fee_assignments'))
        for index in FeeAssignment.__table__.indexes:
            index.create(connection)
        return
    raise RuntimeError(f'Unsupported database for fee assignment uniqueness migration: {connection.dialect.name}')
