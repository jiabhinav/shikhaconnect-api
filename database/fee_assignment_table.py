"""Remove the retired admission amount while preserving class assignments."""
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
