import argparse
import json
import sys
import uvicorn
from alembic import command
from alembic.config import Config

from home_os.config import Settings
from home_os.db.session import create_db_engine_and_session
from home_os.security.audit import verify_audit_chain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="homeos", description="HOME OS Core Server CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve_p = sub.add_parser("serve", help="Run the HOME OS FastAPI backend server")
    serve_p.add_argument("--host", default=None, help="Bind host (defaults to 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=None, help="Bind port (defaults to 8420)")
    serve_p.add_argument("--tls", action="store_true", help="Enable HTTPS using local hub TLS certificate")

    sub.add_parser("migrate", help="Apply Alembic database migrations to head")
    sub.add_parser("verify-audit", help="Cryptographically verify the SHA-256 hash-chained audit log")

    args = parser.parse_args(argv)
    settings = Settings()

    if args.cmd == "migrate":
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", settings.resolved_database_url)
        command.upgrade(cfg, "head")
        print(f"Database migrated successfully: {settings.resolved_database_url}")
        return 0

    if args.cmd == "verify-audit":
        _engine, SessionLocal = create_db_engine_and_session(settings.resolved_database_url)
        with SessionLocal() as db:
            report = verify_audit_chain(db)
            print(json.dumps(report.__dict__, indent=2))
            return 0 if report.valid else 2

    if args.cmd == "serve":
        host = args.host or settings.bind_host
        port = args.port or settings.bind_port
        ssl_kwargs = {}
        if args.tls:
            from home_os.security.crypto import ensure_local_tls_certificate
            cert_info = ensure_local_tls_certificate(settings.tls_dir)
            ssl_kwargs = {
                "ssl_certfile": str(cert_info.cert_path),
                "ssl_keyfile": str(cert_info.key_path),
            }
        uvicorn.run("home_os.main:app", host=host, port=port, **ssl_kwargs)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
