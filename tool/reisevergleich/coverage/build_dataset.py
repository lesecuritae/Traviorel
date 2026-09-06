from __future__ import annotations

import argparse
import csv
import io
import re
import struct
import zipfile
from pathlib import Path

MAGIC = b"FWCOV01\0"
MIN_NORTH, MAX_NORTH = 52358, 61014
MIN_EAST, MAX_EAST = 2803, 9212
WIDTH = MAX_EAST - MIN_EAST + 1
HEIGHT = MAX_NORTH - MIN_NORTH + 1
NO_DATA = 15
CELL_PATTERN = re.compile(r"^100mN(\d+)E(\d+)$")


def _set_nibble(grid: bytearray, index: int, value: int) -> None:
    position, high = divmod(index, 2)
    if high:
        grid[position] = (grid[position] & 0x0F) | (value << 4)
    else:
        grid[position] = (grid[position] & 0xF0) | value


def build(source: Path, destination: Path) -> dict[str, int]:
    grid = bytearray([0xFF]) * ((WIDTH * HEIGHT + 1) // 2)
    rows = 0
    with zipfile.ZipFile(source) as archive:
        names = [name for name in archive.namelist() if name.endswith("_MobilfunkMonitoring.csv")]
        if len(names) != 1:
            raise ValueError("expected exactly one MobilfunkMonitoring CSV in source archive")
        with archive.open(names[0]) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            required = {"id", "lte", "nr_gesamt"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("official CSV schema is missing required columns")
            for row in reader:
                match = CELL_PATTERN.fullmatch(row["id"])
                if not match:
                    raise ValueError(f"invalid grid identifier: {row['id']!r}")
                north, east = map(int, match.groups())
                if not (MIN_NORTH <= north <= MAX_NORTH and MIN_EAST <= east <= MAX_EAST):
                    raise ValueError(f"grid identifier outside documented German extent: {row['id']}")
                # The public CSV exposes operator counts per technology, not
                # operator identities. max(4G, 5G) is therefore a conservative
                # lower bound for distinct broadband networks in this cell.
                networks = max(int(row["lte"]), int(row["nr_gesamt"]))
                if not 0 <= networks <= 4:
                    raise ValueError(f"unexpected broadband network count: {networks}")
                index = (north - MIN_NORTH) * WIDTH + (east - MIN_EAST)
                _set_nibble(grid, index, networks)
                rows += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    header = MAGIC + struct.pack("<4I", MIN_NORTH, MAX_NORTH, MIN_EAST, MAX_EAST)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(header + grid)
    temporary.replace(destination)
    return {"rows": rows, "bytes": destination.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Traviorel coverage raster from the official BNetzA CSV ZIP")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(build(args.source, args.destination))


if __name__ == "__main__":
    main()
