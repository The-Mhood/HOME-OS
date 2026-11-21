from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def create_db_engine_and_session(database_url: str):
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
    engine = create_engine(
        database_url,
        connect_args=connect_args,
        future=True,
        pool_pre_ping=True,
    )

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.close()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return engine, SessionLocal
