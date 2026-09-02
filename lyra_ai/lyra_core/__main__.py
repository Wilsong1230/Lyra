"""python -m lyra_core — the daemon process.

Selects the LLM backend, boots the Runtime (one core, one store, one
listener on 127.0.0.1:DAEMON_PORT) and runs until SIGINT/SIGTERM, closing
the store cleanly. Exits nonzero, with the cause as the final log line,
if it cannot start or if the turn path fails.

    python -m lyra_core                     # auto-select backend from env
    python -m lyra_core --backend ollama --model llama3:latest
    python -m lyra_core --init-store        # create an empty store if none exists
    python -m lyra_core --report            # read-only self-report; no daemon starts
    python -m lyra_core --report --days 30
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import signal
import sys
from pathlib import Path

from lyra_core.config import DAEMON_HOST, DAEMON_PORT, LOG_PATH

log = logging.getLogger("lyra_core")


def _load_dotenv() -> None:
    """Same loader as main.py (the CLI's entrypoint), for the same reason:
    the API keys the backend needs live in .env, not in the environment."""
    for candidate in (Path(".env"), Path.home() / ".env"):
        if candidate.is_file():
            with candidate.open() as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = re.match(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=["\']?(.*?)["\']?\s*$', line)
                    if m:
                        os.environ.setdefault(m.group(1), m.group(2))
            break


def _configure_logging(log_path: Path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    to_file = logging.FileHandler(log_path, encoding="utf-8")
    to_file.setFormatter(fmt)
    root.addHandler(to_file)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from lyra.backends import BACKENDS

    parser = argparse.ArgumentParser(prog="python -m lyra_core", description="Lyra daemon")
    parser.add_argument("--backend", choices=list(BACKENDS), help="LLM backend to use")
    parser.add_argument("--model", help="Model name (overrides backend default)")
    parser.add_argument("--init-store", action="store_true",
                        help="Create an empty memory store if none exists, then start")
    parser.add_argument("--log-file", type=Path, default=LOG_PATH,
                        help=f"Also log to this file (default {LOG_PATH})")
    parser.add_argument("--list-backends", action="store_true",
                        help="Show which backends are available and exit")
    parser.add_argument("--report", action="store_true",
                        help="Render a read-only self-report to stdout and exit "
                             "(starts no daemon, listener, or core)")
    parser.add_argument("--days", type=int, default=None,
                        help="Self-report window in days (default 7; only with --report)")
    return parser.parse_args(argv)


def _select_backend(args: argparse.Namespace):
    from lyra.backends import auto_select_backend, create_backend

    if args.backend:
        backend = create_backend(args.backend)  # ValueError names the missing key
    else:
        backend = auto_select_backend()  # RuntimeError names what to set
    if args.model:
        backend.default_model = args.model
    return backend


async def _serve(args: argparse.Namespace) -> int:
    from lyra_core.runtime import Runtime

    backend = _select_backend(args)
    rt = Runtime(backend, host=DAEMON_HOST, port=DAEMON_PORT, init_store=args.init_store)

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _request_stop(signame: str) -> None:
        log.info("received %s; closing the store", signame)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop, sig.name)
        except NotImplementedError:
            # Windows: ProactorEventLoop has no add_signal_handler. signal.signal
            # still delivers SIGINT (Ctrl-C) on the main thread; hop back onto
            # the loop so the store is closed from the loop's own thread.
            signal.signal(
                sig,
                lambda signum, _frame: loop.call_soon_threadsafe(
                    _request_stop, signal.Signals(signum).name
                ),
            )
    return await rt.run_forever(stop)


def _run_report(days: int | None) -> int:
    """CP-C change 4: read-only, no daemon/listener/core touched — report.py
    itself never imports Runtime, TurnHandler, or the transport, and this
    branch does not either."""
    from lyra.memory import DEFAULT_DB_PATH as HISTORY_PATH
    from lyra_core.report import DEFAULT_WINDOW_DAYS, collect_from_path, render
    from lyra_memory.config import RUNS_PATH, STORE_PATH

    window = days if days is not None else DEFAULT_WINDOW_DAYS
    try:
        measurements = asyncio.run(
            collect_from_path(STORE_PATH, RUNS_PATH, HISTORY_PATH, window_days=window)
        )
    except Exception as exc:
        print(f"could not open store read-only at {STORE_PATH}: {exc}", file=sys.stderr)
        return 1
    print(render(measurements, window))
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = _parse_args(argv)

    if args.list_backends:
        from lyra.backends import BACKENDS, available_backends

        avail = available_backends()
        for name in BACKENDS:
            print(f"  [{'+' if name in avail else '-'}] {name}")
        return 0

    if args.report:
        return _run_report(args.days)

    _configure_logging(args.log_file)
    log.info("lyra_core starting pid=%d", os.getpid())
    try:
        return asyncio.run(_serve(args))
    except Exception as exc:
        # Traceback first, cause last: the final log line must name it.
        log.error("startup failed", exc_info=True)
        log.critical("FATAL: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
