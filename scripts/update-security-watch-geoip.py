#!/usr/bin/env python3
"""Fetch, validate, and atomically promote the free monthly DB-IP Lite databases."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE = "https://download.db-ip.com/free"
MAX_COMPRESSED = 100 * 1024 * 1024
MAX_DATABASE = 300 * 1024 * 1024
TIMEOUT = 30


def month_arg(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError("month must be a valid YYYY-MM calendar month")
    year, month_number = int(value[:4]), int(value[5:])
    try:
        dt.date(year, month_number, 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must be a valid YYYY-MM calendar month") from exc
    return value


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Hermes-Security-Watch/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, destination.open("wb") as output:
        final = response.geturl()
        if not final.startswith("https://") or final.split("/", 3)[2] != "download.db-ip.com":
            raise RuntimeError("download redirected outside the official HTTPS DB-IP host")
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_COMPRESSED:
                raise RuntimeError("download exceeded compressed size budget")
            output.write(chunk)
        if total == 0:
            raise RuntimeError("download was empty")


def validate_mmdb(path: Path, expected: str) -> None:
    try:
        import maxminddb
    except ImportError as exc:
        raise RuntimeError("python3-maxminddb is required to validate GeoIP databases") from exc
    try:
        reader = maxminddb.open_database(str(path))
        try:
            database_type = reader.metadata().database_type.lower()
        finally:
            reader.close()
    except Exception as exc:
        raise RuntimeError(f"invalid MaxMind database {path.name}: {exc}") from exc
    if expected not in database_type:
        raise RuntimeError(f"unexpected database type for {path.name}: {database_type}")


def run(data_dir: Path, month: str) -> None:
    if data_dir.is_symlink():
        raise RuntimeError("data directory must not be a symlink")
    data_dir.mkdir(parents=True, exist_ok=True)
    targets = {"city": data_dir / "city.mmdb", "asn": data_dir / "asn.mmdb"}
    if any(path.is_symlink() for path in (*targets.values(), data_dir / "manifest.json")):
        raise RuntimeError("refusing symlink GeoIP target")
    with tempfile.TemporaryDirectory(prefix=".geoip-update-", dir=data_dir) as tmp:
        work = Path(tmp)
        staged: dict[str, Path] = {}
        hashes: dict[str, str] = {}
        for kind in ("city", "asn"):
            filename = f"dbip-{kind}-lite-{month}.mmdb.gz"
            archive = work / filename
            unpacked = work / f"{kind}.mmdb"
            download(f"{BASE}/{filename}", archive)
            with gzip.open(archive, "rb") as source, unpacked.open("wb") as output:
                total = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DATABASE:
                        raise RuntimeError("database exceeded decompressed size budget")
                    output.write(chunk)
            validate_mmdb(unpacked, kind)
            os.chmod(unpacked, 0o644)
            digest = hashlib.sha256()
            with unpacked.open("rb") as database:
                for chunk in iter(lambda: database.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[kind] = digest.hexdigest()
            staged[kind] = unpacked
        manifest = work / "manifest.json"
        manifest.write_text(json.dumps({"month": month, "city_sha256": hashes["city"], "asn_sha256": hashes["asn"]}, sort_keys=True) + "\n")
        os.chmod(manifest, 0o644)
        # Both candidates pass validation before any live snapshot is touched.
        # Keep hard-linked last-good files beside the live paths (not in `work`)
        # so a process interruption cannot leave readers with a missing path or
        # discard the rollback data when TemporaryDirectory is cleaned up.
        backups: dict[Path, Path] = {}
        promoted: list[Path] = []
        try:
            manifest_target = data_dir / "manifest.json"
            for target in (*targets.values(), manifest_target):
                if target.exists():
                    backup = data_dir / f".{target.name}.last-good"
                    if backup.is_symlink() or (backup.exists() and not backup.is_file()):
                        raise RuntimeError(f"unsafe GeoIP rollback target {backup.name}")
                    backup.unlink(missing_ok=True)
                    os.link(target, backup)
                    backups[target] = backup
            for kind, target in targets.items():
                os.replace(staged[kind], target)
                promoted.append(target)
            os.replace(manifest, manifest_target)
            promoted.append(manifest_target)
        except Exception:
            for target in reversed(promoted):
                backup = backups.get(target)
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
            for target, backup in backups.items():
                backup.unlink(missing_ok=True)
            raise
        else:
            for backup in backups.values():
                backup.unlink(missing_ok=True)
    print(f"Updated DB-IP Lite city and ASN snapshots for {month}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", type=month_arg, help="calendar month YYYY-MM (default: current UTC month)")
    parser.add_argument("--directory", type=Path, default=Path("/var/lib/hermes-security-watch/geoip"))
    args = parser.parse_args()
    month = args.month or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    try:
        run(args.directory, month)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
