#!/usr/bin/env python3
"""Inspect, or copy frozen runtime Redis DBs 1/2 without changing the source.

URLs come exclusively from SOURCE_RUNTIME_REDIS_URL/TARGET_RUNTIME_REDIS_URL.
Default mode is read-only. Applying requires --apply --writers-stopped and empty
target databases. Redis 7+ and synchronized server clocks are required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from redis import Redis
from redis.exceptions import RedisError

READ_KEY = """
local payload = redis.call('DUMP', KEYS[1])
if not payload then return {} end
local kind = redis.call('TYPE', KEYS[1]).ok
local values
if kind == 'string' then values = {redis.call('GET', KEYS[1])}
elseif kind == 'hash' then values = redis.call('HGETALL', KEYS[1])
elseif kind == 'set' then values = redis.call('SMEMBERS', KEYS[1])
elseif kind == 'list' then values = redis.call('LRANGE', KEYS[1], 0, -1)
elseif kind == 'zset' then values = redis.call('ZRANGE', KEYS[1], 0, -1, 'WITHSCORES')
else return redis.error_reply('Unsupported runtime Redis data type') end
return {payload, redis.call('PEXPIRETIME', KEYS[1]), kind, values}
"""
DELETE_OWNED = """
if redis.call('DUMP', KEYS[1]) == ARGV[1]
   and redis.call('PEXPIRETIME', KEYS[1]) == tonumber(ARGV[2]) then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class MigrationError(RuntimeError):
    """Messages must never contain credentials, Redis keys, or values."""


@dataclass(frozen=True)
class Record:
    key: bytes
    payload: bytes = field(compare=False)
    expires_at: int
    kind: bytes
    value_digest: bytes


def client_for_database(url: str, database: int) -> Redis:
    parsed = urlsplit(url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise MigrationError("Expected an explicit Redis URL")
    # Query-string db/decode_responses options could override our DB/binary contract.
    if parsed.query or parsed.fragment:
        raise MigrationError("Redis migration URLs must not contain query strings or fragments")
    address = urlunsplit(parsed._replace(path=f"/{database}"))
    return Redis.from_url(address, decode_responses=False, socket_timeout=10, socket_connect_timeout=3)


def server_time_ms(client: Redis) -> int:
    seconds, microseconds = client.time()
    return seconds * 1000 + microseconds // 1000


def read_record(client: Redis, key: bytes) -> Record | None:
    result = client.eval(READ_KEY, 1, key)
    if not result:
        return None
    payload, expires_at, kind, values = result
    if kind == b"set":
        values = sorted(values)
    elif kind == b"hash":
        pairs = sorted(zip(values[::2], values[1::2]))
        values = [value for pair in pairs for value in pair]
    digest = hashlib.sha256()
    for value in values:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return Record(key, payload, int(expires_at), kind, digest.digest())


def snapshot(client: Redis) -> list[Record]:
    records = {}
    for key in client.scan_iter(count=200):
        record = read_record(client, key)
        if record:
            records[key] = record
    return list(records.values())


def live(records: list[Record], now: int) -> dict[bytes, Record]:
    return {row.key: row for row in records if row.expires_at == -1 or row.expires_at > now}


def audit(records: list[Record]) -> dict:
    digest = hashlib.sha256()
    for row in sorted(records, key=lambda value: value.key):
        # Length-prefix binary keys and payloads; no raw values leave this process.
        for value in (row.key, row.kind, row.value_digest, str(row.expires_at).encode()):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    expirations = [row.expires_at for row in records if row.expires_at > 0]
    return {
        "keys": len(records),
        "types": dict(Counter(row.kind.decode("ascii") for row in records)),
        "expiring": len(expirations),
        "permanent": len(records) - len(expirations),
        "earliest_expiry_ms": min(expirations, default=None),
        "latest_expiry_ms": max(expirations, default=None),
        "binary_and_absolute_ttl_sha256": digest.hexdigest(),
    }


def migrate_databases(pairs: list[tuple[Redis, Redis]], *, apply=False, writers_stopped=False) -> list[dict]:
    if apply and not writers_stopped:
        raise MigrationError("Stop every source and target writer before applying")
    snapshots = []
    for source, target in pairs:
        if source.info("server")["run_id"] == target.info("server")["run_id"]:
            raise MigrationError("Source and target must be separate Redis instances")
        if abs(server_time_ms(source) - server_time_ms(target)) > 100:
            raise MigrationError("Redis server clocks differ by more than 100 milliseconds")
        snapshots.append(snapshot(source))
    report = [
        {"source": audit(records), "target": audit(snapshot(target)), "applied": False}
        for records, (_, target) in zip(snapshots, pairs)
    ]
    if not apply:
        return report
    # No keyspace-wide Redis transaction spans both DBs. Exclusive writer freeze
    # is mandatory; RESTORE without REPLACE additionally rejects any collision.
    if any(target.dbsize() != 0 for _, target in pairs):
        raise MigrationError("Every target database must be empty before copying")
    created = []
    try:
        for records, (source, target) in zip(snapshots, pairs):
            for row in records:
                if row.expires_at != -1 and row.expires_at <= server_time_ms(target):
                    continue
                target.restore(
                    row.key, 0 if row.expires_at == -1 else row.expires_at, row.payload, replace=False, absttl=True
                )
                created.append((target, row))
                # RESTORE may change unordered hash/set internal serialization.
                # Capture this attempt's actual target DUMP for owner-safe cleanup.
                restored = read_record(target, row.key)
                if restored is not None:
                    if restored != row:
                        raise MigrationError("Target changed during copying")
                    created[-1] = (target, restored)
        # Verify both databases after the complete copy, rather than trusting a
        # first database that could have changed while copying the second.
        verified_report = []
        for records, (source, target) in zip(snapshots, pairs):
            observed_source = snapshot(source)
            observed_target = snapshot(target)
            now = max(server_time_ms(source), server_time_ms(target))
            expected = live(records, now)
            if live(observed_source, now) != expected:
                raise MigrationError("Source changed during copying; keep writers stopped and retry")
            if live(observed_target, now) != expected:
                raise MigrationError("Target binary data or absolute expiry verification failed")
            verified_report.append(
                {
                    "source": audit(list(expected.values())),
                    "target": audit(list(live(observed_target, now).values())),
                    "applied": True,
                }
            )
        return verified_report
    except (RedisError, MigrationError):
        # Only remove keys whose binary value and expiry still belong to this
        # attempt. Foreign/changed keys are retained and require manual review.
        cleanup_failed = False
        for target, row in reversed(created):
            try:
                removed = target.eval(DELETE_OWNED, 1, row.key, row.payload, row.expires_at)
                if not removed and target.exists(row.key):
                    cleanup_failed = True
            except RedisError:
                cleanup_failed = True
        for _, target in pairs:
            try:
                # A lost RESTORE reply may hide a committed key. Do not claim
                # complete cleanup while any target data is left behind.
                if target.dbsize():
                    cleanup_failed = True
            except RedisError:
                cleanup_failed = True
        if cleanup_failed:
            raise MigrationError("Copy failed; target cleanup incomplete; do not switch services") from None
        raise MigrationError("Copy failed; owned target keys removed; source not modified by this tool") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    source_url = os.environ.get("SOURCE_RUNTIME_REDIS_URL")
    target_url = os.environ.get("TARGET_RUNTIME_REDIS_URL")
    if not source_url or not target_url:
        parser.error("Set SOURCE_RUNTIME_REDIS_URL and TARGET_RUNTIME_REDIS_URL in the environment")
    clients = []
    try:
        pairs = []
        for database in (1, 2):
            source = client_for_database(source_url, database)
            target = client_for_database(target_url, database)
            clients.extend((source, target))
            pairs.append((source, target))
        print(
            json.dumps(
                {
                    "databases": [1, 2],
                    "results": migrate_databases(
                        pairs,
                        apply=args.apply,
                        writers_stopped=args.writers_stopped,
                    ),
                },
                indent=2,
            )
        )
        return 0
    except (MigrationError, RedisError, ValueError) as exc:
        # Redis errors can contain key names or URL credentials.
        message = str(exc) if isinstance(exc, MigrationError) else "Redis migration operation failed"
        print(json.dumps({"status": "failed", "reason": message}))
        return 1
    finally:
        for client in clients:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
