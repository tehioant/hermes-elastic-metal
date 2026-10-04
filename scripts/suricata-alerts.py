#!/usr/bin/env python3
"""Strict Suricata EVE alert metadata allowlist; no application data forwarding."""
import argparse
import ipaddress
import json


def sanitize(event):
    if not isinstance(event, dict) or event.get('event_type') != 'alert':
        return None
    alert = event.get('alert')
    if not isinstance(alert, dict):
        return None
    try:
        ipaddress.ip_address(event['src_ip'])
        ipaddress.ip_address(event['dest_ip'])
        if not isinstance(event.get('timestamp'), str) or len(event['timestamp']) > 64:
            return None
        if event.get('proto') not in ('TCP', 'UDP', 'ICMP', 'IPv6-ICMP', 'SCTP'):
            return None
        if type(alert.get('signature_id')) is not int or not 0 < alert['signature_id'] < 2**32:
            return None
        if type(alert.get('severity')) is not int or not 1 <= alert['severity'] <= 255:
            return None
        for key in ('signature', 'category', 'action'):
            if not isinstance(alert.get(key), str) or len(alert[key]) > 512:
                return None
        result = {key: event[key] for key in ('timestamp', 'event_type', 'src_ip', 'dest_ip', 'proto')}
        for key in ('src_port', 'dest_port'):
            if key in event:
                if type(event[key]) is not int or not 0 <= event[key] <= 65535:
                    return None
                result[key] = event[key]
        result['alert'] = {key: alert[key] for key in ('signature_id', 'signature', 'category', 'severity', 'action')}
        return result
    except (KeyError, ValueError):
        return None


class Reader:
    """Resume by inode/offset, drain renamed files, and rotate bounded output."""
    def __init__(self, source, output, state, rotation_state=None):
        import logging
        from logging.handlers import RotatingFileHandler
        from pathlib import Path
        self.source, self.state = Path(source), Path(state)
        self.file = None
        self.resume = True
        self.rotation_state = Path(rotation_state) if rotation_state else None
        self.waiting_for_quiescence = False
        self.discarding = False
        self.emitted = self.rejected = 0
        self.handler = RotatingFileHandler(output, maxBytes=10 * 1024 * 1024, backupCount=4)
        self.handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger = logging.Logger('suricata-alert-metadata')
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.INFO)
        # Logging normally swallows write failures; fail instead of losing alerts silently.
        def fail_write(record):
            raise OSError('failed to write bounded Suricata alert output')
        self.handler.handleError = fail_write

    def quiesced(self, stat):
        if self.rotation_state is None:
            return False
        try:
            saved = json.loads(self.rotation_state.read_text())
            return (saved.get('device'), saved.get('inode')) == (stat.st_dev, stat.st_ino)
        except (FileNotFoundError, ValueError):
            return False

    def tick(self):
        import os
        if self.file is None:
            try:
                saved = json.loads(self.state.read_text()) if self.resume else {}
            except (FileNotFoundError, ValueError):
                saved = {}
            candidates = [self.source]
            if saved:
                # Only local numeric, uncompressed archives; never follow archive symlinks.
                candidates += sorted(path for path in self.source.parent.glob(self.source.name + '.*')
                                     if path.name[len(self.source.name) + 1:].isdigit()
                                     and not path.is_symlink())
            for path in candidates:
                try:
                    file = path.open('rb')
                except FileNotFoundError:
                    continue
                stat = os.fstat(file.fileno())
                if not saved or (saved.get('device'), saved.get('inode')) == (stat.st_dev, stat.st_ino):
                    self.file = file
                    offset = saved.get('offset')
                    if type(offset) is int and 0 <= offset <= stat.st_size:
                        self.file.seek(offset)
                    break
                file.close()
            if self.file is None:
                if saved:
                    raise RuntimeError('checkpointed EVE inode missing; preserve archives and recover before restarting')
                return
            self.resume = False
        # Bound each iteration; large bursts must not starve checkpointing or health.
        for _ in range(1000):
            start = self.file.tell()
            line = self.file.readline(65537)
            if not line:
                break
            if self.discarding or len(line) > 65536:
                self.discarding = not line.endswith(b'\n')
                self.rejected += 1
                continue
            if not line.endswith(b'\n'):
                old = os.fstat(self.file.fileno())
                try:
                    current = self.source.stat()
                except FileNotFoundError:
                    current = old
                if (old.st_dev, old.st_ino) != (current.st_dev, current.st_ino) and self.quiesced(old):
                    self.rejected += 1  # Incomplete final record on a rotated inode.
                else:
                    self.file.seek(start)
                break
            try:
                event = sanitize(json.loads(line))
            except (ValueError, UnicodeDecodeError):
                event = None
            if event:
                self.logger.info(json.dumps(event, separators=(',', ':')))
                self.emitted += 1
            else:
                self.rejected += 1
        stat = os.fstat(self.file.fileno())
        saved = {'inode': stat.st_ino, 'device': stat.st_dev, 'offset': self.file.tell()}
        temporary = self.state.with_suffix('.tmp')
        temporary.write_text(json.dumps(saved))
        temporary.replace(self.state)
        try:
            current = self.source.stat()
        except FileNotFoundError:
            return
        # EOF is temporary until a root-owned witness proves the writer was stopped.
        rotated = (current.st_ino, current.st_dev) != (stat.st_ino, stat.st_dev)
        if rotated and not self.quiesced(stat):
            if not self.waiting_for_quiescence:
                import sys
                print('EVE rotation lacks writer-quiescence witness; retaining old inode; use coordinated logrotate',
                      file=sys.stderr, flush=True)
                self.waiting_for_quiescence = True
        elif rotated and self.file.tell() >= stat.st_size:
            self.file.close()
            self.file = None
            self.discarding = False
            self.waiting_for_quiescence = False
        elif current.st_ino == stat.st_ino and current.st_size < self.file.tell():
            self.file.seek(0)
            self.discarding = False

    def close(self):
        if self.file:
            self.file.close()
        self.handler.close()


def main():
    import signal
    import time
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--rotation-state', help='Root-protected writer-quiescence witness')
    args = parser.parse_args()
    running = True
    def stop(signum, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    reader = Reader(args.input, args.output, args.state, args.rotation_state)
    last_health = 0
    try:
        while running:
            reader.tick()
            now = time.monotonic()
            if now - last_health >= 60:
                print(json.dumps({'emitted': reader.emitted, 'rejected_or_non_alert': reader.rejected}), file=sys.stderr, flush=True)
                last_health = now
            time.sleep(0.25)
    finally:
        reader.close()


if __name__ == '__main__':
    main()
