"""Tenant isolation.

Isolation is enforced centrally at the ORM layer, not left to each query:

* Every SELECT / UPDATE / DELETE touching a tenant-owned model is automatically
  restricted to the active tenant.
* Writing a row for a different tenant raises `TenantIsolationError`.
* Fail-closed: querying tenant-owned data with *no* tenant chosen raises
  `TenantContextRequired`. Platform-wide work (super administrator, CLI,
  scheduled jobs) must say so explicitly with `platform_scope()`.

Usage::

    with tenant_context(tenant.id):
        Membership.query.all()          # only this tenant's rows

    with platform_scope():
        Tenant.query.count()            # everything (platform administration)
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event
from sqlalchemy.sql import visitors
from sqlalchemy.sql.expression import TableClause
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria


class TenantContextRequired(RuntimeError):
    """Tenant-owned data was queried without choosing a tenant or platform scope."""


class TenantIsolationError(PermissionError):
    """An attempt was made to write data belonging to another tenant."""


_UNSET = object()
_PLATFORM = object()
_scope: ContextVar = ContextVar("portia_tenant_scope", default=_UNSET)


def current_tenant_id() -> int | None:
    value = _scope.get()
    return value if isinstance(value, int) else None


def in_platform_scope() -> bool:
    return _scope.get() is _PLATFORM


def scope_is_set() -> bool:
    return _scope.get() is not _UNSET


@contextmanager
def tenant_context(tenant_id: int):
    if not isinstance(tenant_id, int) or isinstance(tenant_id, bool):
        raise TypeError("tenant_id must be an integer")
    token = _scope.set(tenant_id)
    try:
        yield tenant_id
    finally:
        _scope.reset(token)


@contextmanager
def platform_scope():
    token = _scope.set(_PLATFORM)
    try:
        yield
    finally:
        _scope.reset(token)


def reset_scope() -> None:
    """Called at the start of every request so no scope leaks between requests."""
    _scope.set(_UNSET)


def _tenant_owned_classes():
    from app.models.audit import AuditLog
    from app.models.base import TenantScopedMixin
    from app.models.tenant import Tenant

    return TenantScopedMixin, Tenant, AuditLog


def _is_tenant_owned(cls) -> bool:
    scoped_mixin, tenant_cls, audit_cls = _tenant_owned_classes()
    return isinstance(cls, type) and (issubclass(cls, (scoped_mixin, tenant_cls, audit_cls)))


def _owned_table_names() -> set[str]:
    from app.extensions import db

    names = set()
    for mapper in db.Model.registry.mappers:
        if _is_tenant_owned(mapper.class_):
            names.add(mapper.local_table.name)
    return names


def _touched_tables(statement) -> set[str]:
    """Names of tenant-owned tables used anywhere in a statement (joins and subqueries included)."""
    owned = _owned_table_names()
    found = set()
    for element in visitors.iterate(statement):
        if isinstance(element, TableClause) and element.name in owned:
            found.add(element.name)
    return found


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get("include_all_tenants"):
        if in_platform_scope():
            return
        raise TenantIsolationError("include_all_tenants is only allowed in platform scope")

    scope = _scope.get()
    if scope is _PLATFORM:
        return
    if scope is _UNSET:
        if state.is_column_load:
            return  # refreshing attributes of a row that was already legitimately loaded
        touched = {m.class_.__name__ for m in state.all_mappers if _is_tenant_owned(m.class_)}
        touched |= _touched_tables(state.statement)
        if touched:
            raise TenantContextRequired(
                f"Query on tenant-owned data ({', '.join(sorted(touched))}) without a tenant context. "
                "Use tenant_context(...) or platform_scope()."
            )
        return

    # Tenant scope: always attach the criteria. They apply wherever the entity appears:
    # the main query, joins, subqueries (e.g. Query.count()), lazy and column loads.
    tid = scope
    scoped_mixin, tenant_cls, audit_cls = _tenant_owned_classes()
    state.statement = state.statement.options(
        with_loader_criteria(scoped_mixin, lambda cls: cls.tenant_id == tid, include_aliases=True),
        with_loader_criteria(tenant_cls, tenant_cls.id == tid, include_aliases=True),
        with_loader_criteria(audit_cls, audit_cls.tenant_id == tid, include_aliases=True),
    )


@event.listens_for(Session, "before_flush")
def _guard_tenant_writes(session: Session, flush_context, instances) -> None:
    scope = _scope.get()
    if scope is _PLATFORM:
        return
    scoped_mixin, tenant_cls, audit_cls = _tenant_owned_classes()

    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, (scoped_mixin, audit_cls)):
            if scope is _UNSET:
                raise TenantContextRequired(
                    f"Writing {type(obj).__name__} requires a tenant context or platform scope."
                )
            if obj.tenant_id is None and obj in session.new:
                obj.tenant_id = scope
            elif obj.tenant_id != scope:
                raise TenantIsolationError(
                    f"{type(obj).__name__} belongs to another tenant; write refused."
                )
        elif isinstance(obj, tenant_cls):
            if scope is _UNSET:
                raise TenantContextRequired("Changing a tenant requires platform scope or its own context.")
            if obj in session.new:
                raise TenantIsolationError("New tenants can only be created in platform scope.")
            if obj.id != scope:
                raise TenantIsolationError("Cannot modify another tenant's workspace.")


def use_platform_scope_for_request() -> None:
    """For blueprints whose every view is platform administration (scope resets next request)."""
    _scope.set(_PLATFORM)


def use_tenant_for_request(tenant_id: int) -> None:
    """Pin the current request to one tenant workspace (used from Stage 2 after sign-in)."""
    if not isinstance(tenant_id, int) or isinstance(tenant_id, bool):
        raise TypeError("tenant_id must be an integer")
    _scope.set(tenant_id)
