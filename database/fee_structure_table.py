"""Remove the retired carry-forward setting from existing fee structures."""
from sqlalchemy import inspect, text


def remove_fee_structure_carry_forward(connection):
    if not inspect(connection).has_table('fee_structures'):
        return
    if 'carry_forward' not in {column['name'] for column in inspect(connection).get_columns('fee_structures')}:
        return
    if connection.dialect.name == 'postgresql':
        connection.execute(text('SELECT pg_advisory_xact_lock(731904218)'))
        if 'carry_forward' not in {column['name'] for column in inspect(connection).get_columns('fee_structures')}:
            return
    connection.execute(text('ALTER TABLE fee_structures DROP COLUMN carry_forward'))
