"""Organization membership and inherited enterprise grants."""
from sqlalchemy import select

from .database import Department, Member, Model, ModelGrant
from .security import PlatformError


def ancestors(db, member):
    if not member or not member.department_id:
        return set()
    table = Department.__table__
    tree = select(table.c.id, table.c.parent_id).where(table.c.id == member.department_id).cte(recursive=True)
    tree = tree.union_all(select(table.c.id, table.c.parent_id).join(tree, table.c.id == tree.c.parent_id))
    return set(db.scalars(select(tree.c.id)))


def checked_units(db, ids):
    if not isinstance(ids, list) or len(ids) > 10000 or any(not isinstance(i, str) for i in ids):
        raise PlatformError("invalid_departments", "请选择有效部门。")
    if ids and set(ids) != set(db.scalars(select(Department.id).where(Department.id.in_(ids)))):
        raise PlatformError("unknown_department", "部门已变化，请刷新核对。", 409)
    return sorted(set(ids))


def available_models(db, actor, *, enabled_only=False):
    direct = set(db.scalars(select(ModelGrant.model_id).where(ModelGrant.member_id == actor)))
    units = ancestors(db, db.get(Member, actor))
    query = select(Model).order_by(Model.name)
    if enabled_only:
        query = query.where(Model.enabled)
    return [m for m in db.scalars(query) if m.id in direct or units.intersection(m.department_ids)]


def model_allowed(db, member, model):
    return bool(model and model.enabled and member and member.enabled and
                (db.get(ModelGrant, (member.id, model.id)) or
                 model.department_ids and ancestors(db, member).intersection(model.department_ids)))
