import argparse
import asyncio
import sys
from pathlib import Path

from homeos_linux_agent.client import LinuxDeviceAgent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="homeos-linux-agent", description="HOME OS Native Linux Device Agent")
    parser.add_argument("--hub", default="http://127.0.0.1:8420", help="HOME OS Hub base URL")
    parser.add_argument(
        "--state-dir",
        default=str(Path.home() / ".config" / "homeos-agent"),
        help="Directory for encrypted agent credential store",
    )
    parser.add_argument(
        "--share-dir",
        action="append",
        default=[],
        help="User-authorized directory exposed for file browsing/transfer (may be specified multiple times)",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)
    pair_p = sub.add_parser("pair", help="Complete authenticated pairing with HOME OS Hub")
    pair_p.add_argument("--session", required=True, help="Pairing session ID")
    pair_p.add_argument("--pin", required=True, help="6-digit PIN or QR token")
    pair_p.add_argument("--name", default=None, help="Display name for this Linux computer")

    run_p = sub.add_parser("run", help="Start live authenticated agent connection")
    run_p.add_argument("--interval", type=float, default=8.0, help="Telemetry interval in seconds")

    args = parser.parse_args(argv)
    roots = [Path(p) for p in args.share_dir] if args.share_dir else [Path.home() / "HomeOS-Shared"]
    agent = LinuxDeviceAgent(
        hub_http_url=args.hub,
        state_dir=Path(args.state_dir),
        authorized_roots=roots,
        telemetry_interval=getattr(args, "interval", 8.0),
    )

    if args.cmd == "pair":
        res = asyncio.run(agent.enroll(pairing_session_id=args.session, pin_or_token=args.pin, device_name=args.name))
        print(f"Enrolled successfully as device_id={res['device']['id']} ({res['device']['name']})")
        return 0

    if args.cmd == "run":
        try:
            asyncio.run(agent.run_forever())
        except KeyboardInterrupt:
            agent.stop()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
