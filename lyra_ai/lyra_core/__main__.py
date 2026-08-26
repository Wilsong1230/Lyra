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
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    await rt.run_forever(stop)


if __name__ == "__main__":
    asyncio.run(main())
