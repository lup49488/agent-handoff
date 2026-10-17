"""Store settings, from ``stores.ini``.

Each store has a section::

    [store:B]
    utc_offset = -03:30
    day_cutoff_hour = 4
    currency = CAD

For a one-off run against another setting, an environment variable overrides
the file: ``REPORT_B_UTC_OFFSET=-04:00`` changes store B's offset only.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

DEFAULT_PATH = Path("stores.ini")
_PREFIX = "store:"
_FIELDS = ("utc_offset", "day_cutoff_hour", "currency")


@dataclass(frozen=True)
class StoreSettings:
    name: str
    utc_offset: str
    day_cutoff_hour: int
    currency: str


def _override(store: str, field: str, environ: Mapping[str, str]) -> Optional[str]:
    return environ.get("REPORT_%s_%s" % (store.upper(), field.upper()))


def load_stores(path: Path = DEFAULT_PATH, environ: Optional[Mapping[str, str]] = None) -> Dict[str, StoreSettings]:
    """Every store in the file, with any environment overrides applied."""
    environ = os.environ if environ is None else environ
    parser = configparser.ConfigParser()
    if not parser.read(str(path), encoding="utf-8"):
        raise FileNotFoundError("no store settings at %s" % path)
    stores: Dict[str, StoreSettings] = {}
    for section in parser.sections():
        if not section.startswith(_PREFIX):
            continue
        name = section[len(_PREFIX):].strip()
        values = {}
        for field in _FIELDS:
            value = _override(name, field, environ)
            if value is None:
                if not parser.has_option(section, field):
                    raise ValueError("store %s has no %s" % (name, field))
                value = parser.get(section, field)
            values[field] = value.strip()
        stores[name] = StoreSettings(
            name=name,
            utc_offset=values["utc_offset"],
            day_cutoff_hour=int(values["day_cutoff_hour"]),
            currency=values["currency"],
        )
    if not stores:
        raise ValueError("%s defines no [store:...] sections" % path)
    return stores
