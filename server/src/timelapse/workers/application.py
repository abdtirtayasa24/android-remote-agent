from __future__ import annotations

import asyncio

from timelapse.services.process_shell import run_process_shell


def main() -> None:
    asyncio.run(run_process_shell("worker"))


if __name__ == "__main__":
    main()
