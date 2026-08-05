from __future__ import annotations

import argparse
import importlib
import shlex
import sys
from pathlib import Path
from typing import Callable

from .config import load_config
from .config import resolve_profile_dir
from .config import with_browser_overrides
from .config import with_reservation_overrides

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="opentable-bot",
        description="Book and check OpenTable reservations with a persistent browser profile.",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config JSON. Defaults to config.json.",
    )
    parser.add_argument(
        "--engine",
        choices=["auto", "camoufox", "playwright"],
        help="Override browser.engine from config.json.",
    )
    parser.add_argument(
        "--profile-dir",
        help="Override browser.profile_dir from config.json.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("login", help="Open OpenTable and wait for manual login.")
    subparsers.add_parser("check", help="Print reservations from the logged-in account.")
    subparsers.add_parser("admin-check", help="Open GuestCenter reservations and print visible text.")
    subparsers.add_parser("profile-check", help="Print browser profile diagnostics and GuestCenter login state.")
    health_parser = subparsers.add_parser("health-check", help="Check config, dependencies, daemon, and n8n connectivity.")
    health_parser.add_argument("--daemon-url", default="http://127.0.0.1:8765", help="Local browser daemon base URL.")
    health_parser.add_argument("--jobs-url", help="Optional n8n queue URL to test with GET.")
    health_parser.add_argument("--status-url", help="Optional n8n status URL to validate without sending a request.")
    daemon_reload_parser = subparsers.add_parser(
        "daemon-reload",
        help="Reload automation code in the running browser daemon.",
    )
    daemon_reload_parser.add_argument(
        "--daemon-url",
        default="http://127.0.0.1:8765",
        help="Local browser daemon base URL.",
    )
    subparsers.add_parser("admin-debug-datepicker", help="Open date picker and print detected calendar controls.")
    subparsers.add_parser("session", help="Keep one browser open and run commands interactively.")
    service_parser = subparsers.add_parser(
        "service",
        help="Keep one browser open and accept reservation jobs over HTTP.",
    )
    service_parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    service_parser.add_argument("--port", type=int, default=8765, help="HTTP bind port.")
    poll_parser = subparsers.add_parser(
        "poll",
        help="Keep one browser open and poll an n8n webhook for reservation jobs.",
    )
    poll_parser.add_argument("--jobs-url", required=True, help="n8n URL returning the next queued job or jobs.")
    poll_parser.add_argument("--status-url", help="Optional n8n URL receiving running/completed/failed status updates.")
    poll_parser.add_argument("--interval", type=float, default=5.0, help="Seconds between empty queue polls.")
    poll_parser.add_argument("--once", action="store_true", help="Fetch/process one poll response and exit.")
    poll_client_parser = subparsers.add_parser(
        "poll-client",
        help="Poll n8n and forward jobs to a local browser daemon without opening Camoufox.",
    )
    poll_client_parser.add_argument("--jobs-url", required=True, help="n8n URL returning the next queued job or jobs.")
    poll_client_parser.add_argument("--daemon-url", default="http://127.0.0.1:8765", help="Local browser daemon base URL.")
    poll_client_parser.add_argument("--status-url", help="Optional n8n URL receiving queue handoff status.")
    poll_client_parser.add_argument("--status-method", choices=["POST", "PUT"], default="POST", help="HTTP method for status updates.")
    poll_client_parser.add_argument("--interval", type=float, default=5.0, help="Seconds between empty queue polls.")
    poll_client_parser.add_argument("--once", action="store_true", help="Fetch/forward one poll response and exit.")
    admin_date_parser = subparsers.add_parser(
        "admin-date",
        help="Open GuestCenter make-reservation modal and select only the date.",
    )
    _add_booking_override_args(admin_date_parser)
    book_parser = subparsers.add_parser("book", help="Start the configured booking flow.")
    _add_booking_override_args(book_parser)
    book_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Click the final reservation confirmation button.",
    )
    admin_book_parser = subparsers.add_parser(
        "admin-book",
        help="Create the configured reservation from GuestCenter/admin.",
    )
    _add_booking_override_args(admin_book_parser)
    admin_book_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Click the final admin save/create button.",
    )

    args = parser.parse_args(argv)
    config = load_config(args.config)
    config = with_browser_overrides(
        config,
        engine=args.engine,
        profile_dir=args.profile_dir,
    )
    config = _config_for_command(config, args)
    artifacts_dir = Path(config.path.parent / "artifacts")

    if args.command == "poll-client":
        from .service import run_poll_client

        return run_poll_client(
            jobs_url=args.jobs_url,
            daemon_url=args.daemon_url,
            status_url=args.status_url,
            status_method=args.status_method,
            interval_seconds=args.interval,
            once=args.once,
        )

    if args.command == "health-check":
        from .health import run_health_check

        return run_health_check(
            config,
            daemon_url=args.daemon_url,
            jobs_url=args.jobs_url,
            status_url=args.status_url,
        )

    if args.command == "daemon-reload":
        from .service import _http_json

        response = _http_json(
            "POST",
            f"{args.daemon_url.rstrip('/')}/reload",
            {},
        )
        print(response.get("message", "Automation code reloaded."))
        return 0

    try:
        from .browser import open_browser
        from . import opentable
    except ModuleNotFoundError as exc:
        if exc.name in {"playwright", "camoufox"}:
            print(
                "Missing browser dependencies. Run: pip install -r requirements.txt",
                file=sys.stderr,
            )
            return 2
        raise

    try:
        with open_browser(config) as context:
            if args.command == "session":
                return _run_session(
                    parser,
                    context,
                    config,
                    artifacts_dir,
                    opentable,
                )
            if args.command == "service":
                from .service import run_service

                return run_service(
                    context,
                    config,
                    artifacts_dir,
                    host=args.host,
                    port=args.port,
                )
            if args.command == "poll":
                from .service import run_poller

                return run_poller(
                    context,
                    config,
                    artifacts_dir,
                    jobs_url=args.jobs_url,
                    status_url=args.status_url,
                    interval_seconds=args.interval,
                    once=args.once,
                )
            return _run_once(context, config, args, artifacts_dir, opentable)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


def _config_for_command(config, args):
    if args.command in {"book", "admin-book", "admin-date"}:
        return with_reservation_overrides(
            config,
            date_value=args.date,
            time_value=args.time,
            party_size=args.party_size,
            first_name=args.first_name,
            last_name=args.last_name,
            email=args.email,
            phone=args.phone,
            special_request=args.special_request,
        )
    return config


def _run_once(context, config, args, artifacts_dir: Path, opentable) -> int:
    artifacts_dir.mkdir(exist_ok=True)

    if args.command == "login":
        opentable.login_interactively(context, config)
        print("Login session saved in the persistent browser profile.")
        return 0

    if args.command == "check":
        reservations = opentable.check_reservations(context, config)
        print("\n".join(f"- {item}" for item in reservations))
        opentable.save_artifacts(context, artifacts_dir, "check")
        return 0

    if args.command == "admin-check":
        reservations = opentable.admin_check_reservations(context, config)
        print("\n".join(f"- {item}" for item in reservations))
        opentable.save_artifacts(context, artifacts_dir, "admin-check")
        return 0

    if args.command == "profile-check":
        profile_dir = resolve_profile_dir(config)
        print(f"Profile path: {profile_dir}")
        for name in ("parent.lock", "cookies.sqlite", "storage.sqlite", "camoufox-fingerprint.json"):
            path = profile_dir / name
            if path.exists():
                print(f"{name}: exists, size={path.stat().st_size}, modified={path.stat().st_mtime}")
            else:
                print(f"{name}: missing")
        reservations = opentable.admin_check_reservations(context, config)
        print("\n".join(f"- {item}" for item in reservations))
        opentable.save_artifacts(context, artifacts_dir, "profile-check")
        return 0

    if args.command == "admin-debug-datepicker":
        details = opentable.admin_debug_datepicker(context, config)
        print("\n".join(f"- {item}" for item in details))
        opentable.save_artifacts(context, artifacts_dir, "admin-debug-datepicker")
        return 0

    if args.command == "admin-date":
        result = opentable.admin_select_date(context, config)
        print(f"{result.status}: {result.message}")
        print(result.url)
        opentable.save_artifacts(context, artifacts_dir, "admin-date")
        return 0

    if args.command == "book":
        result = opentable.book_reservation(context, config, confirm=args.confirm)
        print(f"{result.status}: {result.message}")
        print(result.url)
        opentable.save_artifacts(context, artifacts_dir, "book")
        return 0

    if args.command == "admin-book":
        result = opentable.admin_book_reservation(context, config, confirm=args.confirm)
        print(f"{result.status}: {result.message}")
        print(result.url)
        opentable.save_artifacts(context, artifacts_dir, "admin-book")
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


def _run_session(parser, context, config, artifacts_dir: Path, opentable) -> int:
    print("Session mode: one browser stays open for all commands.")
    print("Commands: login, admin-check, admin-debug-datepicker, admin-date [options], admin-book [options], timeout MS, reload, help, exit")

    while True:
        try:
            line = input("opentable> ").strip()
        except EOFError:
            print()
            if not sys.stdin.isatty():
                return 0
            print("Session is still open. Type `exit` to close the browser.")
            continue
        except KeyboardInterrupt:
            print()
            print("Session is still open. Type `exit` to close the browser.")
            continue

        if not line:
            continue
        if line.lower() in {"exit", "quit", "q"}:
            return 0
        if line.lower() == "reload":
            opentable = importlib.reload(opentable)
            config = load_config(str(config.path))
            context.set_default_timeout(config.browser.timeout_ms)
            print("Reloaded automation code and config without closing the browser.")
            continue
        if line.lower().startswith("timeout "):
            try:
                timeout_ms = int(line.split(maxsplit=1)[1])
                if timeout_ms < 1000:
                    raise ValueError
            except ValueError:
                print("Usage: timeout 8000")
                continue
            context.set_default_timeout(timeout_ms)
            print(f"Session command timeout set to {timeout_ms} ms.")
            continue
        if line.lower() in {"help", "?"}:
            print("Examples:")
            print("  login")
            print("  admin-check")
            print("  admin-debug-datepicker")
            print("  admin-date --date 2026-08-06")
            print("  admin-book --date 2026-07-20 --time 7pm --party-size 4")
            print("  admin-book --date next-month-20 --time 7pm --party-size 2 --confirm")
            print("  timeout 8000")
            print("  reload")
            print("  exit")
            continue

        try:
            args = parser.parse_args(shlex.split(line))
            if args.command == "session":
                print("Already in session mode.")
                continue
            command_config = _config_for_command(config, args)
            _run_once(context, command_config, args, artifacts_dir, opentable)
        except KeyboardInterrupt:
            print()
            print("Command interrupted. Browser session is still open.")
        except SystemExit:
            print("Invalid command. Type `help` for examples.")
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)


def _add_booking_override_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--date",
        help=(
            "Booking date. Accepts YYYY-MM-DD, today, tomorrow, +Nd, "
            "or next-month-DD."
        ),
    )
    parser.add_argument(
        "--time",
        help="Booking time. Accepts 24-hour time like 19:00 or text like 7pm.",
    )
    parser.add_argument("--party-size", type=int, help="Guest count.")
    parser.add_argument("--first-name", help="Guest first name.")
    parser.add_argument("--last-name", help="Guest last name.")
    parser.add_argument("--email", help="Guest email.")
    parser.add_argument("--phone", help="Guest phone.")
    parser.add_argument("--special-request", help="Reservation notes/request.")


if __name__ == "__main__":
    raise SystemExit(main())
