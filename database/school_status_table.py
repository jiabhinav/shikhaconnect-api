"""Initialize school status, including columns added manually in pgAdmin."""
from database.advisory_lock import acquire_transaction_lock
from sqlalchemy import inspect, text


def ensure_school_status(connection):
    if not inspect(connection).has_table("schools"):
        return
    if connection.dialect.name == "postgresql":
        acquire_transaction_lock(connection, 731904218)
    columns = {column["name"] for column in inspect(connection).get_columns("schools")}
    if "status" not in columns:
        connection.execute(text("ALTER TABLE schools ADD COLUMN status BOOLEAN NOT NULL DEFAULT true"))
    else:
        connection.execute(text("UPDATE schools SET status = true WHERE status IS NULL"))
        if connection.dialect.name == "postgresql":
            connection.execute(text("ALTER TABLE schools ALTER COLUMN status SET DEFAULT true"))
            connection.execute(text("ALTER TABLE schools ALTER COLUMN status SET NOT NULL"))
