import os
import re
from pathlib import Path


def _load_dotenv() -> None:
    for candidate in (Path(".env"), Path.home() / ".env"):
        if candidate.is_file():
            with candidate.open() as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = re.match(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=["\']?(.*?)["\']?\s*$', line)
                    if m:
                        key, val = m.group(1), m.group(2)
                        os.environ.setdefault(key, val)
            break


_load_dotenv()

from lyra.cli import main

if __name__ == "__main__":
    main()
