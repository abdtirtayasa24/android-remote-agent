from __future__ import annotations

import logging
import time


def configure_logging(service_name: str, level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format=(
            f"%(asctime)sZ service={service_name} level=%(levelname)s %(name)s %(message)s"
        ),
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )

    logging.Formatter.converter = time.gmtime
