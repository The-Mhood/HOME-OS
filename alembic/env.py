import os
from pathlib import Path
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from home_os.db.models import Base

config = context.config

if config.config_file_name is not None and config.get_section("loggers"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Resolve the target URL the same way the application does.

    Priority:
      1. A URL already set on the alembic config (e.g. by the CLI or tests).
      2. HOMEOS_DATABASE_URL env var (the CI/CD pipeline exports this). The
         aiosqlite variant used by some tooling is normalized to the sync
         sqlite driver the app uses.
      3. Settings-derived URL (HOMEOS_DATA_DIR or default data dir + home_os.db).

    Never silently write into a repo-relative ./data/home_os.db — that directory
    may not exist, and the fallback is unrelated to the running app's data.
    """
    explicit = (config.get_main_option("sqlalchemy.url") or "").strip()
    if explicit:
        url = explicit.replace("sqlite+aiosqlite://", "sqlite://")
    else:
        env_url = os.environ.get("HOMEOS_DATABASE_URL")
        if env_url:
            url = env_url.replace("sqlite+aiosqlite://", "sqlite://")
        else:
            from home_os.config import Settings

            settings = Settings()
            url = settings.resolved_database_url

    # sqlite cannot create missing parent directories — make sure the target
    # directory exists (same behaviour the app uses for data_dir).
    if url.startswith("sqlite:///"):
        db_file = Path(url.removeprefix("sqlite:///")).expanduser()
        db_file.parent.mkdir(parents=True, exist_ok=True)
    return url


_RESOLVED_URL = _resolve_database_url()
if config.get_main_option("sqlalchemy.url") != _RESOLVED_URL:
    config.set_main_option("sqlalchemy.url", _RESOLVED_URL)


def run_migrations_offline() -> None:
    url = _RESOLVED_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = dict(config.get_section(config.config_ini_section, {}))
    configuration["sqlalchemy.url"] = _RESOLVED_URL
    url = _RESOLVED_URL
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=url.startswith("sqlite"),
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
