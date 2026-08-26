"""python -m lyra_core — standalone mind process.

Boots the perception + dreaming loops together and shuts down cleanly on
SIGINT (Ctrl-C) or SIGTERM.
"""
from __future__ import annotations

import asyncio
import signal

from lyra_core.runtime import Runtime


async def main() -> None:
    rt = Runtime()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows: ProactorEventLoop has no add_signal_handler. signal.signal
            # still delivers SIGINT (Ctrl-C) on the main thread; hop back onto the
            # loop to set the event so shutdown stays clean.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    await rt.run_forever(stop)


if __name__ == "__main__":
    asyncio.run(main())
