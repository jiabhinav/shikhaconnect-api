from pydantic_settings import BaseSettings
from sqlalchemy import Enum, String, create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base


class Settings(BaseSettings):
    DATABASE_HOST: str = "localhost"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "shikhaconnect"
    DATABASE_USER: str = "ravishukla"
    DATABASE_PASSWORD: str = ""


    class Config:
        env_file = ".env"


settings = Settings()


if settings.DATABASE_PASSWORD:
    DATABASE_URL = (
        f"postgresql+psycopg2://"
        f"{settings.DATABASE_USER}:"
        f"{settings.DATABASE_PASSWORD}@"
        f"{settings.DATABASE_HOST}:"
        f"{settings.DATABASE_PORT}/"
        f"{settings.DATABASE_NAME}"
    )
else:
    DATABASE_URL = (
        f"postgresql+psycopg2://"
        f"{settings.DATABASE_USER}@"
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
    if isinstance(column.type, String):
        length = getattr(column.type, "length", None)
        return "VARCHAR" if length is None else f"VARCHAR({length})"

    if isinstance(column.type, Enum):
        return column.type.name

    return column.type.compile(dialect=engine.dialect)


def _default_value_sql(column):
    default_value = None
    if column.default is not None:
        default_value = getattr(column.default, "arg", column.default)
        if hasattr(default_value, "value"):
            default_value = default_value.value

    if default_value is None:
        if not column.nullable and not column.primary_key:
            if isinstance(column.type, String):
                return " DEFAULT ''"
            if isinstance(column.type, Enum):
                enum_values = [str(v.value) if hasattr(v, "value") else str(v) for v in getattr(column.type, "enums", [])]
                if enum_values:
                    return f" DEFAULT '{enum_values[0]}'"
        return ""

    if isinstance(default_value, str):
        return f" DEFAULT '{default_value}'"
    if default_value is None:
        return ""
    return f" DEFAULT {default_value}"


def sync_missing_columns():
    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue

        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}

        for column in table.columns:
            if column.name in existing_columns:
                continue

            if isinstance(column.type, Enum):
                enum_values = [str(v.value) if hasattr(v, "value") else str(v) for v in getattr(column.type, "enums", [])]
                enum_name = column.type.name
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{enum_name}') "
                            f"THEN CREATE TYPE {enum_name} AS ENUM ({', '.join(f"'{value}'" for value in enum_values)}); END IF; END $$;"
                        )
                    )

            column_sql = _column_sql_type(column)
            add_sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {column_sql}{_default_value_sql(column)};'

            with engine.begin() as conn:
                conn.execute(text(add_sql))

            if not column.nullable and not column.primary_key:
                fill_value = "''" if isinstance(column.type, String) else "'0'" if isinstance(column.type, Enum) else "0"
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            f"UPDATE \"{table_name}\" SET \"{column.name}\" = {fill_value} WHERE \"{column.name}\" IS NULL;"
                        )
                    )
                    conn.execute(
                        text(
                            f"ALTER TABLE \"{table_name}\" ALTER COLUMN \"{column.name}\" SET NOT NULL;"
                        )
                    )


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()