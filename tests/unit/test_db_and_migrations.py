from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from home_os.db.session import create_db_engine_and_session


def test_alembic_migrations_upgrade_and_downgrade(tmp_data_dir: Path):
    db_path = tmp_data_dir / "migrated.db"
    db_url = f"sqlite:///{db_path}"

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "head")
    engine, _ = create_db_engine_and_session(db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    expected_tables = {
        "users",
        "user_sessions",
        "rooms",
        "devices",
        "device_credentials",
        "pairing_sessions",
        "device_capabilities",
        "device_permissions",
        "command_executions",
        "communication_items",
        "media_items",
        "active_media_sessions",
        "file_transfers",
        "automation_rules",
        "automation_runs",
        "integration_connections",
        "audit_logs",
    }
    assert expected_tables.issubset(tables)

    command.downgrade(cfg, "base")
    inspector_after = inspect(engine)
    remaining = set(inspector_after.get_table_names()) - {"alembic_version"}
    assert remaining == set()

    command.upgrade(cfg, "head")
    inspector_reup = inspect(engine)
    assert expected_tables.issubset(set(inspector_reup.get_table_names()))
