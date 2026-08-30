from datetime import date, datetime
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings
from sqlalchemy import Boolean, Date, DateTime, Enum, Numeric, String, create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base


class Settings(BaseSettings):
    # DATABASE_HOST: str = "localhost"
    # DATABASE_PORT: int = 5432
    # DATABASE_NAME: str = "shikhaconnect"
    # DATABASE_USER: str = "ravishukla"
    # DATABASE_PASSWORD: str = ""

    DATABASE_HOST: str = "217.21.91.156"
    DATABASE_PORT: int = 3306
    DATABASE_NAME: str = "u671685499_connect"
    DATABASE_USER: str = "u671685499_connect"
    DATABASE_PASSWORD: str = "Shiksha@#$12345"



    class Config:
        env_file = ".env"


settings = Settings()

# PostgreSQL connection (previous setup)
# if settings.DATABASE_PASSWORD:
#     DATABASE_URL = (
#         f"postgresql+psycopg2://"
#         f"{settings.DATABASE_USER}:"
#         f"{settings.DATABASE_PASSWORD}@"
#         f"{settings.DATABASE_HOST}:"
#         f"{settings.DATABASE_PORT}/"
#         f"{settings.DATABASE_NAME}"
#     )
# else:
#     DATABASE_URL = (
#         f"postgresql+psycopg2://"
#         f"{settings.DATABASE_USER}@"
#         f"{settings.DATABASE_HOST}:"
#         f"{settings.DATABASE_PORT}/"
#         f"{settings.DATABASE_NAME}"
#     )

# MySQL connection for remote server
user = quote_plus(settings.DATABASE_USER)
password = quote_plus(settings.DATABASE_PASSWORD)

if settings.DATABASE_PASSWORD:
    DATABASE_URL = (
        f"mysql+pymysql://"
        f"{user}:{password}@"
        f"{settings.DATABASE_HOST}:"
        f"{settings.DATABASE_PORT}/"
        f"{settings.DATABASE_NAME}"
    )
else:
    DATABASE_URL = (
        f"mysql+pymysql://"
        f"{user}@"
        f"{settings.DATABASE_HOST}:"
        f"{settings.DATABASE_PORT}/"
        f"{settings.DATABASE_NAME}"
    )


engine = create_engine(DATABASE_URL)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


Base = declarative_base()


def _column_sql_type(column):
    # Use native MySQL definitions, including DECIMAL and ENUM values.
    return column.type.compile(dialect=engine.dialect)


def _missing_column_value(column):
    """Temporary value for a new NOT NULL column on a populated table."""
    if column.default is not None:
        default_value = getattr(column.default, "arg", column.default)
        if callable(default_value):
            default_value = None
        if hasattr(default_value, "value"):
            default_value = default_value.value
        if default_value is not None:
            return default_value

    if isinstance(column.type, Enum):
        values = list(getattr(column.type, "enums", []))
        return values[0] if values else ""
    if isinstance(column.type, String):
        return ""
    if isinstance(column.type, Boolean):
        return False
    if isinstance(column.type, DateTime):
        return datetime(1970, 1, 1)
    if isinstance(column.type, Date):
        return date(1970, 1, 1)
    if isinstance(column.type, Numeric):
        return 0
    return 0


def sync_missing_columns():
    """Add missing model columns using additive, MySQL-compatible DDL."""
    inspector = inspect(engine)
    quote = engine.dialect.identifier_preparer.quote

    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue

        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}

        for column in table.columns:
            if column.name in existing_columns:
                continue
            if column.primary_key:
                raise RuntimeError(
                    f"Cannot safely auto-add primary key {table_name}.{column.name}; use a migration"
                )

            quoted_table = quote(table_name)
            quoted_column = quote(column.name)
            column_sql = _column_sql_type(column)

            with engine.begin() as conn:
                # Nullable-first works even when the existing table has rows.
                conn.execute(
                    text(
                        f"ALTER TABLE {quoted_table} "
                        f"ADD COLUMN {quoted_column} {column_sql} NULL"
                    )
                )
                if not column.nullable:
                    conn.execute(
                        text(
                            f"UPDATE {quoted_table} SET {quoted_column} = :fill_value "
                            f"WHERE {quoted_column} IS NULL"
                        ),
                        {"fill_value": _missing_column_value(column)},
                    )
                    conn.execute(
                        text(
                            f"ALTER TABLE {quoted_table} "
                            f"MODIFY COLUMN {quoted_column} {column_sql} NOT NULL"
                        )
                    )


def test_db_connection():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
