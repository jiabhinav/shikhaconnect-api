from sqlalchemy import MetaData, Table, select
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.orm import Session


def get_module_names(db: Session, module_ids: list[int]) -> dict[int, str]:
    if not module_ids:
        return {}
    try:
        modules = Table("modules", MetaData(), autoload_with=db.connection())
    except NoSuchTableError:
        return {}
    return dict(db.execute(
        select(modules.c.id, modules.c.name).where(modules.c.id.in_(module_ids))
    ).all())
