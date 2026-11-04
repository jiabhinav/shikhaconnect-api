"""Fee assignment schema migrations."""
from database.advisory_lock import acquire_transaction_lock
from sqlalchemy import BigInteger, MetaData, Table, inspect, select, text
from sqlalchemy.schema import CreateTable


def remove_fee_assignment_admission_amount(connection):
    if not inspect(connection).has_table('fee_assignments'):
        return
    if 'admission_amount' not in {col['name'] for col in inspect(connection).get_columns('fee_assignments')}:
        return
    if connection.dialect.name == 'postgresql':
        acquire_transaction_lock(connection, 731904218)
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


def migrate_fee_assignment_configuration_reference(connection):
    """Reference the configuration anchor row and migrate legacy structure IDs."""
    inspector = inspect(connection)
    if not inspector.has_table('fee_assignments') or not inspector.has_table('fee_configuration_heads'):
        return
    foreign_keys = inspector.get_foreign_keys('fee_assignments')
    legacy_foreign_key = any(
        constraint.get('constrained_columns') == ['fee_configure_id']
        and constraint.get('referred_table') == 'fee_structures'
        for constraint in foreign_keys
    )
    target_foreign_key = any(
        constraint.get('constrained_columns') == ['fee_configure_id']
        and constraint.get('referred_table') == 'fee_configuration_heads'
        and constraint.get('referred_columns') == ['id']
        for constraint in foreign_keys
    )
    column_type = next(
        column['type'] for column in inspector.get_columns('fee_assignments')
        if column['name'] == 'fee_configure_id'
    )
    if target_foreign_key and isinstance(column_type, BigInteger):
        return

    if connection.dialect.name == 'postgresql':
        acquire_transaction_lock(connection, 731904218)
        for constraint in inspect(connection).get_foreign_keys('fee_assignments'):
            if constraint.get('constrained_columns') == ['fee_configure_id'] and constraint.get('referred_table') in {
                    'fee_structures', 'fee_configuration_heads'}:
                name = connection.dialect.identifier_preparer.quote(constraint['name'])
                connection.execute(text(f'ALTER TABLE fee_assignments DROP CONSTRAINT {name}'))
        if not isinstance(column_type, BigInteger):
            connection.execute(text(
                'ALTER TABLE fee_assignments ALTER COLUMN fee_configure_id TYPE BIGINT '
                'USING fee_configure_id::BIGINT'
            ))
    elif connection.dialect.name == 'sqlite':
        if legacy_foreign_key or not isinstance(column_type, BigInteger):
            _rebuild_fee_assignments(connection, include_configuration_foreign_key=False)
    else:
        raise RuntimeError(f'Unsupported database for fee configuration assignment foreign key: {connection.dialect.name}')

    assignments = Table('fee_assignments', MetaData(), autoload_with=connection)
    configurations = Table('fee_configuration_heads', MetaData(), autoload_with=connection)
    anchor_ids = {
        (row['school_id'], row['session_id'], row['id'])
        for row in connection.execute(select(
            configurations.c.id, configurations.c.configuration_id,
            configurations.c.school_id, configurations.c.session_id,
        )).mappings()
        if row['id'] == row['configuration_id']
    }
    should_remap = legacy_foreign_key or not isinstance(column_type, BigInteger)
    assignment_rows = connection.execute(select(assignments)).mappings().all()
    if not should_remap:
        should_remap = any(
            (row['school_id'], row['session_id'], row['fee_configure_id']) not in anchor_ids
            for row in assignment_rows
        )

    if should_remap:
        config_by_scope = {}
        for row in connection.execute(select(
                configurations.c.configuration_id, configurations.c.school_id,
                configurations.c.session_id, configurations.c.fee_structure_id,
        ).order_by(configurations.c.configuration_id)).mappings():
            config_by_scope.setdefault(
                (row['school_id'], row['session_id'], row['fee_structure_id']),
                row['configuration_id'],
            )
        unresolved = []
        for row in assignment_rows:
            old_value = row['fee_configure_id']
            if not legacy_foreign_key and (row['school_id'], row['session_id'], old_value) in anchor_ids:
                continue
            configuration_id = config_by_scope.get((row['school_id'], row['session_id'], old_value))
            if configuration_id is None:
                unresolved.append(row['id'])
                continue
            connection.execute(assignments.update().where(assignments.c.id == row['id']).values(
                fee_configure_id=configuration_id))
        if unresolved:
            raise RuntimeError(
                'Cannot add the fee configuration foreign key: existing fee assignments '
                f'have no matching configuration (assignment IDs: {unresolved})'
            )

    if connection.dialect.name == 'postgresql':
        if not target_foreign_key:
            connection.execute(text(
                'ALTER TABLE fee_assignments ADD CONSTRAINT fk_fee_assignments_fee_configure_id '
                'FOREIGN KEY (fee_configure_id) REFERENCES fee_configuration_heads(id) ON DELETE RESTRICT'
            ))
    else:
        _rebuild_fee_assignments(connection, include_configuration_foreign_key=True)


def _rebuild_fee_assignments(connection, include_configuration_foreign_key):
    """SQLite rebuild helper; staging removes the old reference before remapping."""
    from sqlalchemy.schema import CreateTable
    from models.fee_assignment import FeeAssignment

    table_name = 'fee_assignments'
    temp_name = 'fee_assignments_rebuilt'
    metadata = MetaData()
    replacement = FeeAssignment.__table__.to_metadata(metadata, name=temp_name)
    if not include_configuration_foreign_key:
        for constraint in list(replacement.foreign_key_constraints):
            if any(element.parent.name == 'fee_configure_id' for element in constraint.elements):
                replacement.constraints.remove(constraint)
                replacement.c.fee_configure_id.foreign_keys.clear()
    connection.execute(CreateTable(replacement))
    columns = ', '.join(column.name for column in FeeAssignment.__table__.columns)
    connection.execute(text(
        f'INSERT INTO {temp_name} ({columns}) SELECT {columns} FROM {table_name}'
    ))
    connection.execute(text(f'DROP TABLE {table_name}'))
    connection.execute(text(f'ALTER TABLE {temp_name} RENAME TO {table_name}'))
    for index in FeeAssignment.__table__.indexes:
        index.create(connection)


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
