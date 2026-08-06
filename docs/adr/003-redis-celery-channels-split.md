# ADR-003: Redis/Celery/Channels responsibility split

## Status: Accepted
## Date: 2026-03

## Context

Redis serves multiple distinct roles in this project: Django cache backend, Celery
message broker, Celery result backend, Channels real-time layer, and direct data
store (online-presence sorted set, world-chat history list, rate-limit counters).
Mixing all traffic in a single logical database risks key collisions, makes
monitoring opaque, and couples failure domains.

## Decision

Assign each concern its own Redis logical database (configurable via separate
environment variables), defaulting to distinct `/N` paths on the same instance:

| Role              | Setting               | Default        |
|-------------------|-----------------------|----------------|
| Celery broker     | `REDIS_BROKER_URL`    | `REDIS_URL/0`  |
| Celery results    | `REDIS_RESULT_URL`    | broker URL     |
| Channel layer     | `REDIS_CHANNEL_URL`   | `REDIS_URL/1`  |
| Django cache      | `REDIS_CACHE_URL`     | `REDIS_URL/2`  |

All URLs inherit `REDIS_PASSWORD` when no credentials are embedded in the URL.
`_redis_url_with_password()` injects auth transparently so that operators only need
to set the password once.

Celery additionally defines five task queues (`default`, `battle`, `timer`,
`timer_scan`, `timer_maintenance`) with explicit route mappings in
`celery_conf.py`. The `timer` queue handles latency-sensitive single-record
completion tasks, `timer_scan` handles user-related batch scans, and
`timer_maintenance` isolates virtual-player, market, arena-reserve, and cleanup
work so a slow maintenance sweep cannot delay routine state scans. `battle`
isolates CPU-intensive report generation.

Production infrastructure validation (`_validate_production_infrastructure`) enforces
that non-debug, non-test environments must use a real database engine, a named
database, and authenticated Redis connections.

## Consequences

- **Development:** A single `redis-server` with default config serves all roles;
  no additional setup beyond `REDIS_URL` is needed.
- **Production:** Operators can point each role at a separate Redis instance or
  cluster by overriding individual URL variables, enabling independent scaling,
  memory limits, and persistence policies.
- **Monitoring:** Each logical database can be observed independently; `DBSIZE`,
  `INFO`, and slow-log are not polluted across concerns.
- **Trade-off:** More environment variables to manage; default overlap means a
  single-instance failure still affects all roles unless explicitly separated.
