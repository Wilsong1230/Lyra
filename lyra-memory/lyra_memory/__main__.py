from __future__ import annotations
import asyncio
import signal
from lyra_memory import MemorySystem


async def main() -> None:
    memory = MemorySystem()
    await memory.start()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    await stop.wait()
    await memory.stop()


if __name__ == "__main__":
    asyncio.run(main())
