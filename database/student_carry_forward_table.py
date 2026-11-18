from sqlalchemy import func, inspect, literal, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.schema import CreateColumn, CreateIndex

from database.advisory_lock import acquire_transaction_lock
from models.session import Session as SchoolSession
from models.student_carry_forward import CARRY_FORWARD_TAG, StudentCarryForward


def migrate_student_carry_forwards(connection):
    table = StudentCarryForward.__table__
    unique_index = next(index for index in table.indexes if index.unique)
    column_names = ("paid_amount", "unpaid_amount", "month")
    existing = {column["name"]: column for column in inspect(connection).get_columns(table.name)}
    new_default = f"'{CARRY_FORWARD_TAG}'"
    postgres = connection.dialect.name == "postgresql"
    needs_tag_update = new_default not in (existing["tag"]["default"] or "")
    old_index_name = "uq_student_carry_forward_student_session"
    existing_indexes = {index["name"] for index in inspect(connection).get_indexes(table.name)}
    has_unique_index = unique_index.name in existing_indexes
    if (
        all(name in existing for name in column_names)
        and not needs_tag_update
        and has_unique_index
        and old_index_name not in existing_indexes
    ):
        return

    if postgres:
        acquire_transaction_lock(connection, 731904218)
        existing = {column["name"]: column for column in inspect(connection).get_columns(table.name)}
        needs_tag_update = new_default not in (existing["tag"]["default"] or "")

    for name in ("paid_amount", "unpaid_amount"):
        if name not in existing:
            definition = CreateColumn(table.c[name]).compile(dialect=connection.dialect)
            connection.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {definition}")

    if "month" not in existing:
        connection.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN month VARCHAR(7)")
        session_month = (
            select(session_start_month(connection))
            .where(SchoolSession.id == table.c.session_id)
            .scalar_subquery()
        )
        connection.execute(table.update().values(month=session_month))
        if postgres:
            connection.exec_driver_sql(f"ALTER TABLE {table.name} ALTER COLUMN month SET NOT NULL")

    if not has_unique_index:
        connection.execute(CreateIndex(unique_index, if_not_exists=True))
    if old_index_name in existing_indexes:
        connection.exec_driver_sql(f"DROP INDEX IF EXISTS {old_index_name}")

    if needs_tag_update:
        connection.execute(table.update().where(table.c.tag == "Carry Forward").values(tag=CARRY_FORWARD_TAG))
        if postgres:
            connection.exec_driver_sql(f"ALTER TABLE {table.name} ALTER COLUMN tag SET DEFAULT {new_default}")


def session_start_month(connection):
    if connection.dialect.name == "postgresql":
        return func.to_char(SchoolSession.start_date, "YYYY-MM")
    return func.strftime("%Y-%m", SchoolSession.start_date)


def backfill_student_carry_forwards(connection):
    from models.student import Student

    table = StudentCarryForward.__table__
    existing = select(table.c.id).where(
        table.c.school_id == Student.school_id,
        table.c.session_id == Student.session_id,
        table.c.student_id == Student.id,
    ).exists()
    missing = (
        select(
            Student.school_id,
            Student.session_id,
            Student.id,
            session_start_month(connection),
            literal(CARRY_FORWARD_TAG),
        )
        .join(SchoolSession, (SchoolSession.id == Student.session_id) & (SchoolSession.school_id == Student.school_id))
        .where(~existing)
    )
    columns = ("school_id", "session_id", "student_id", "month", "tag")
    if connection.dialect.name == "postgresql":
        statement = postgres_insert(table).from_select(columns, missing, include_defaults=False)
        statement = statement.on_conflict_do_nothing(
            index_elements=[table.c.school_id, table.c.student_id, table.c.session_id],
        )
    else:
        statement = table.insert().from_select(columns, missing, include_defaults=False)
    connection.execute(statement)
