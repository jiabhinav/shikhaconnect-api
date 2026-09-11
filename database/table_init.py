"""Register application models and create missing tables in dependency order."""
from importlib import import_module
from pkgutil import walk_packages

from sqlalchemy import inspect, text

from database.database import Base


def register_models():
    import models

    for module in walk_packages(models.__path__, prefix="models."):
        import_module(module.name)


def ensure_all_tables(connection):
    register_models()
    existing = set(inspect(connection).get_table_names())
    if set(Base.metadata.tables).issubset(existing):
        return
    if connection.dialect.name == "postgresql":
        # Recheck within create_all after serializing concurrent initialization.
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    Base.metadata.create_all(bind=connection, checkfirst=True)
