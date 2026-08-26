from __future__ import annotations
import asyncio
import signal
from lyra_memory import MemorySystem


async def main() -> None:
    memory = MemorySystem()
    await memory.start()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows: ProactorEventLoop has no add_signal_handler. signal.signal
            # still delivers SIGINT (Ctrl-C) on the main thread; hop back onto the
            # loop to set the event so the dreaming loop shuts down cleanly.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    await stop.wait()
    await memory.stop()


if __name__ == "__main__":
    asyncio.run(main())
