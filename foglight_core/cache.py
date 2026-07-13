"""Bounded disk cache used by provider fetches."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import sys
import threading
import time


class DiskCache:
    def __init__(
        self,
        root,
        max_bytes=128 * 1024 * 1024,
        max_entries=1000,
        max_entry_bytes=10 * 1024 * 1024,
    ):
        if max_bytes < 1 or max_entries < 0 or max_entry_bytes < 1:
            raise ValueError("cache caps must be positive (entry count may be zero)")
        self.root = str(root)
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.max_entry_bytes = min(max_entry_bytes, max_bytes)
        self._lock = threading.Lock()
        self._last_prune = 0.0
        os.makedirs(self.root, exist_ok=True)
        self.prune()

    def _path(self, key):
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        return os.path.join(self.root, digest + ".bin")

    def _meta_path(self, key):
        return self._path(key) + ".meta"

    def get(self, key, ttl, max_stale=None):
        path = self._path(key)
        metadata_path = self._meta_path(key)
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return None, "miss", 0
        if stat.st_size > self.max_entry_bytes:
            return None, "miss", 0
        age = time.time() - stat.st_mtime
        try:
            with open(path, "rb") as file:
                data = file.read(self.max_entry_bytes + 1)
            if len(data) > self.max_entry_bytes or os.path.getsize(metadata_path) > 64 * 1024:
                return None, "miss", 0
            with open(metadata_path, encoding="utf-8") as file:
                metadata = json.loads(file.read(64 * 1024 + 1))
            if not isinstance(metadata, dict):
                return None, "miss", 0
        except (OSError, ValueError):
            return None, "miss", 0
        timestamp = metadata.get("ts")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            timestamp = stat.st_mtime
        elif not math.isfinite(timestamp):
            timestamp = stat.st_mtime
        if age < ttl:
            return data, "hit", timestamp
        if max_stale is None:
            max_stale = max(3600, ttl * 20)
        if age > max_stale:
            return None, "miss", 0
        return data, "stale", timestamp

    def put(self, key, data, ctype="application/octet-stream"):
        if not isinstance(data, bytes) or len(data) > self.max_entry_bytes:
            return False
        if not isinstance(ctype, str):
            ctype = "application/octet-stream"
        ctype = ctype[:200]
        path = self._path(key)
        metadata_path = self._meta_path(key)
        with self._lock:
            try:
                with open(path + ".tmp", "wb") as file:
                    file.write(data)
                os.replace(path + ".tmp", path)
                with open(metadata_path + ".tmp", "w", encoding="utf-8") as file:
                    json.dump({"ts": time.time(), "ctype": ctype}, file)
                os.replace(metadata_path + ".tmp", metadata_path)
                self.prune(locked=True)
                return True
            except OSError as error:
                sys.stderr.write(f"[cache] put failed: {error}\n")
                return False

    def prune(self, locked=False):
        def prune_locked():
            self._last_prune = time.time()
            try:
                newest = []
                with os.scandir(self.root) as directory:
                    for entry in directory:
                        if entry.name.endswith(".tmp"):
                            try:
                                os.remove(entry.path)
                            except (FileNotFoundError, OSError):
                                pass
                            continue
                        if entry.name.endswith(".bin.meta"):
                            if not os.path.isfile(entry.path[:-5]):
                                try:
                                    os.remove(entry.path)
                                except (FileNotFoundError, OSError):
                                    pass
                            continue
                        if not entry.name.endswith(".bin"):
                            continue
                        try:
                            stat = entry.stat()
                            metadata_stat = os.stat(entry.path + ".meta")
                        except OSError:
                            for candidate in (entry.path, entry.path + ".meta"):
                                try:
                                    os.remove(candidate)
                                except (FileNotFoundError, OSError):
                                    pass
                            continue
                        if (stat.st_size > self.max_entry_bytes
                                or metadata_stat.st_size > 64 * 1024):
                            for candidate in (entry.path, entry.path + ".meta"):
                                try:
                                    os.remove(candidate)
                                except (FileNotFoundError, OSError):
                                    pass
                            continue
                        item = (
                            stat.st_mtime,
                            entry.path,
                            stat.st_size + metadata_stat.st_size,
                        )
                        if self.max_entries == 0:
                            for candidate in (entry.path, entry.path + ".meta"):
                                try:
                                    os.remove(candidate)
                                except (FileNotFoundError, OSError):
                                    pass
                            continue
                        heapq.heappush(newest, item)
                        if len(newest) > self.max_entries:
                            _mtime, path, _size = heapq.heappop(newest)
                            for candidate in (path, path + ".meta"):
                                try:
                                    os.remove(candidate)
                                except FileNotFoundError:
                                    pass
                                except OSError as error:
                                    sys.stderr.write(f"[cache] prune failed: {error}\n")
                kept_bytes = 0
                for _mtime, path, size in sorted(newest, reverse=True):
                    if kept_bytes + size <= self.max_bytes:
                        kept_bytes += size
                        continue
                    for candidate in (path, path + ".meta"):
                        try:
                            os.remove(candidate)
                        except FileNotFoundError:
                            pass
                        except OSError as error:
                            sys.stderr.write(f"[cache] prune failed: {error}\n")
            except OSError as error:
                sys.stderr.write(f"[cache] scan failed: {error}\n")

        if locked:
            prune_locked()
        else:
            with self._lock:
                prune_locked()
