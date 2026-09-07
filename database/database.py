from datetime import date, datetime

from pydantic_settings import BaseSettings
from sqlalchemy import Boolean, Date, DateTime, Enum, Numeric, String, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker, declarative_base


class Settings(BaseSettings):
    DATABASE_URL: str | None = None
    DATABASE_HOST: str | None = None
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str | None = None
    DATABASE_USER: str | None = None
    DATABASE_PASSWORD: str | None = None

    class Config:
        env_file = ".env"


def build_database_url(config: Settings) -> URL:
    """Prefer a hosted database URL; retain separate fields for local use."""
    if config.DATABASE_URL:
        url = make_url(config.DATABASE_URL)
        if url.drivername not in ("postgres", "postgresql", "postgresql+psycopg2"):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
        return url.set(drivername="postgresql+psycopg2")

    required = ("DATABASE_HOST", "DATABASE_NAME", "DATABASE_USER", "DATABASE_PASSWORD")
    missing = [name for name in required if getattr(config, name) is None]
    if missing:
        raise ValueError("Set DATABASE_URL or provide: " + ", ".join(missing))
    return URL.create(
        "postgresql+psycopg2",
        username=config.DATABASE_USER,
        password=config.DATABASE_PASSWORD,
        host=config.DATABASE_HOST,
        port=config.DATABASE_PORT,
        database=config.DATABASE_NAME,
    )


settings = Settings()
DATABASE_URL = build_database_url(settings)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


Base = declarative_base()


def _column_sql_type(column):
    # Compile types for the configured database dialect.
    return column.type.compile(dialect=engine.dialect)


def _missing_column_value(column):
    """Temporary value for a new NOT NULL column on a populated table."""
    if column.default is not None:
        default_value = getattr(column.default, "arg", column.default)
        if callable(default_value):
            default_value = None
        if default_value is not None:
            return default_value

    if isinstance(column.type, Enum):
        values = list(getattr(column.type, "enums", []))
        return column.type.enum_class[values[0]] if column.type.enum_class else (values[0] if values else "")
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
    """Add missing model columns using additive SQL DDL.

    This function is dialect-aware and will issue the appropriate
    ALTER statements for MySQL and PostgreSQL when adding columns
    and setting NOT NULL constraints.
    """
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
                if engine.dialect.name == "postgresql" and isinstance(column.type, Enum):
                    column.type.create(conn, checkfirst=True)
                # Nullable-first works even when the existing table has rows.
                conn.execute(
                    text(
                        f"ALTER TABLE {quoted_table} "
                        f"ADD COLUMN {quoted_column} {column_sql} NULL"
                    )
                )
                if not column.nullable:
                    # Fill existing NULLs with a safe default value.
                    conn.execute(
                        table.update()
                        .where(column.is_(None))
                        .values({column.name: _missing_column_value(column)}),
                    )

                    # Apply NOT NULL using dialect-appropriate SQL.
                    if engine.dialect.name == "mysql":
                        conn.execute(
                            text(
                                f"ALTER TABLE {quoted_table} "
                                f"MODIFY COLUMN {quoted_column} {column_sql} NOT NULL"
                            )
                        )
                    elif engine.dialect.name in ("postgresql", "postgres"):
                        conn.execute(
                            text(
                                f"ALTER TABLE {quoted_table} "
                                f"ALTER COLUMN {quoted_column} SET NOT NULL"
                            )
                        )
                    else:
                        # Fallback: try generic ALTER (may or may not work).
                        conn.execute(
                            text(
                                f"ALTER TABLE {quoted_table} "
                                f"ALTER COLUMN {quoted_column} SET NOT NULL"
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
