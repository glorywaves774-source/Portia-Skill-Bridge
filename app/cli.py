"""Command-line tools: `flask --help` lists them."""
from __future__ import annotations

import json

import click
from flask import Flask
from sqlalchemy import select

from app.extensions import db
from app.models import BillingCycle, OrganizationType, Tenant, TenantKind, User
from app.tenancy import platform_scope


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-plans")
    @click.option("--update", is_flag=True, help="Also overwrite existing plans with the defaults in app/seeds.py.")
    def seed_plans_cmd(update):
        """Create any missing default plans. Edits made in the console are kept unless --update."""
        from app.seeds import seed_plans

        created, updated = seed_plans(update=update)
        click.echo(f"Plans ready: {created} created, {updated} updated.")

    @app.cli.command("create-superadmin")
    @click.option("--email", required=True)
    @click.option("--name", "full_name", required=True)
    @click.password_option(help="At least 10 characters.")
    def create_superadmin(email, full_name, password):
        """Create (or promote) a SaaS Super Administrator account."""
        email = User.normalise_email(email)
        user = db.session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, full_name=full_name)
            db.session.add(user)
        user.is_platform_admin = True
        try:
            user.set_password(password)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="password")
        with platform_scope():
            from app.services import audit
            audit.record("platform.superadmin_set", actor_label="cli", entity_type="user",
                         summary=f"Super administrator: {email}")
            db.session.commit()
        click.echo(f"Super administrator ready: {email}")

    @app.cli.command("demo-data")
    @click.option("--password", default=None, help="Password for every demo account (one is generated if left out).")
    def demo_data(password):
        """Create a sample training-centre workspace to explore. Not for production databases."""
        if app.config.get("ENV_NAME") == "production" or app.config.get("SESSION_COOKIE_SECURE"):
            raise click.ClickException("demo-data is for local and test databases only.")
        from app.demo import build
        try:
            info = build(password=password)
        except (RuntimeError, ValueError) as exc:
            raise click.ClickException(str(exc))
        click.echo(f"Created {info['tenant']}. Sign in with any of these, password {info['password']}:")
        for login in info["logins"]:
            click.echo(f"  {login}")

    @app.cli.command("create-tenant")
    @click.option("--name", required=True)
    @click.option("--kind", type=click.Choice(TenantKind.ALL), default=TenantKind.ORGANIZATION, show_default=True)
    @click.option("--plan", "plan_code", required=True, help="Plan code, e.g. org-starter or ind-plus")
    @click.option("--org-type", type=click.Choice(OrganizationType.ALL), default=None)
    @click.option("--cycle", type=click.Choice(BillingCycle.ALL), default=BillingCycle.MONTHLY, show_default=True)
    @click.option("--email", default=None)
    @click.option("--city", default=None)
    def create_tenant_cmd(name, kind, plan_code, org_type, cycle, email, city):
        """Create a workspace and start its subscription (trial where the plan has one)."""
        from app.services.tenants import TenantError, create_tenant
        from app.services.subscriptions import SubscriptionError

        with platform_scope():
            try:
                tenant = create_tenant(name=name, kind=kind, plan_code=plan_code, organization_type=org_type,
                                       billing_cycle=cycle, email=email, city=city, actor=None)
                db.session.commit()
            except (TenantError, SubscriptionError) as exc:
                db.session.rollback()
                raise click.ClickException(str(exc))
            sub = tenant.current_subscription
            click.echo(f"Created {tenant.code} '{tenant.name}' — {sub.plan.name}, {sub.status_label} "
                       f"until {sub.current_period_end:%d %b %Y}")

    @app.cli.group("tenants")
    def tenants_group():
        """List, suspend and reactivate workspaces."""

    @tenants_group.command("list")
    def tenants_list():
        with platform_scope():
            for t in db.session.scalars(select(Tenant).order_by(Tenant.id)):
                sub = t.current_subscription
                click.echo(f"{t.code}  {t.status:<9}  {t.kind:<12}  {(sub.plan.code if sub else '-'):<17} "
                           f"{(sub.status if sub else '-'):<9}  {t.name}")

    @tenants_group.command("suspend")
    @click.argument("code")
    @click.option("--reason", required=True)
    def tenants_suspend(code, reason):
        from app.services.tenants import TenantError, suspend_tenant
        with platform_scope():
            tenant = _get_tenant(code)
            try:
                suspend_tenant(tenant, reason=reason, actor=None)
            except TenantError as exc:
                raise click.ClickException(str(exc))
            db.session.commit()
            click.echo(f"Suspended {code}.")

    @tenants_group.command("reactivate")
    @click.argument("code")
    def tenants_reactivate(code):
        from app.services.tenants import TenantError, reactivate_tenant
        with platform_scope():
            tenant = _get_tenant(code)
            try:
                reactivate_tenant(tenant, actor=None)
            except TenantError as exc:
                raise click.ClickException(str(exc))
            db.session.commit()
            click.echo(f"Reactivated {code}.")

    @app.cli.group("subscriptions")
    def subscriptions_group():
        """Subscription maintenance."""

    @subscriptions_group.command("process")
    def subscriptions_process():
        """Daily job: billing reminders and renewals, subscription changes, follow-up alerts, queued emails."""
        from app import jobs
        click.echo(json.dumps(jobs.run("daily", jobs.daily), default=str))

    @app.cli.group("notifications")
    def notifications_group():
        """In-app notifications and their emails."""

    @notifications_group.command("send")
    def notifications_send():
        """Email queued notifications. Run every 10 minutes (it's quick when there's nothing to send)."""
        from app import jobs
        from app.services import notifications
        click.echo(json.dumps(jobs.run("notifications", notifications.send_pending)))

    _register_backup_commands(app)


def _register_backup_commands(app):
    @app.cli.group("backup")
    def backup_group():
        """Encrypted database backups."""

    @backup_group.command("create")
    def backup_create():
        """Back up the whole database to storage (backups/…) and delete old backups. Run daily."""
        from app import jobs
        from app.services import backups
        click.echo(json.dumps(jobs.run("backup", backups.create)))

    @backup_group.command("list")
    def backup_list():
        """Show the backups in storage, newest first."""
        from app.services import backups
        for f in backups.listing():
            click.echo(f"{f['key'].split('/')[-1]}\t{backups.human_size(f['size'])}\t{f['modified']:%Y-%m-%d %H:%M}")

    @backup_group.command("verify")
    @click.argument("name")
    def backup_verify(name):
        """Decrypt a backup and check every table's checksum (changes nothing)."""
        from app.services import backups
        try:
            m = backups.verify(backups.read(name))
        except backups.BackupError as exc:
            raise click.ClickException(str(exc))
        rows = sum(t["rows"] for t in m["tables"].values())
        click.echo(f"OK: {rows} rows in {len(m['tables'])} tables, made {m['created_at']} "
                   f"(version {m.get('app_version')}, revision {m.get('revision')}).")

    @backup_group.command("restore")
    @click.argument("name")
    @click.option("--wipe", is_flag=True, help="Delete everything in this database first.")
    @click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
    def backup_restore(name, wipe, yes):
        """Load a backup (a name from `backup list` or a file path) into this database."""
        from app.services import backups
        if wipe and not yes:
            click.confirm("This deletes EVERYTHING in this database and replaces it with the backup. Continue?",
                          abort=True)
        with platform_scope():
            try:
                result = backups.restore(backups.read(name), wipe=wipe)
                db.session.commit()
            except backups.BackupError as exc:
                db.session.rollback()
                raise click.ClickException(str(exc))
        click.echo(f"Restored {result['rows']} rows in {result['tables']} tables from the backup made "
                   f"{result['backup_created_at']}.")

    @app.cli.group("system")
    def system_group():
        """Go-live readiness."""

    @system_group.command("check")
    def system_check():
        """Show the System status checks. Exits with an error if anything is a problem."""
        from app.services import system
        with platform_scope():
            results = system.checks()
        for c in results:
            click.echo(f"[{c['state'].upper():4}] {c['name']}: {c['detail']}")
        if any(c["state"] == "bad" for c in results):
            raise click.ClickException("Some checks failed.")

    @backup_group.command("make-key")
    def backup_make_key():
        """Print a new BACKUP_ENCRYPTION_KEY. Store it safely: backups can't be opened without it."""
        from app.services import backups
        click.echo(backups.make_key())


def _get_tenant(code: str) -> Tenant:
    tenant = db.session.scalar(select(Tenant).where(Tenant.code == code.upper()))
    if tenant is None:
        raise click.ClickException(f"No workspace with code {code}")
    return tenant
