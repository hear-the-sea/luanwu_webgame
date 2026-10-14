# Runtime Redis 隔离与迁移

本配置是可选发布覆盖文件，不会自动改变现有服务器。原 `redis` 继续承载
Celery broker/result 并开启 AOF；新增 `redis_runtime` 承载 Channels DB1 与
Django cache DB2。逻辑分库本身不能隔离磁盘停顿，独立实例才能让运行时通信
不等待 broker 的 AOF 落盘。

## 配置与容量

在生产配置、镜像覆盖文件之后加载 `docker-compose.redis-runtime.yml`。所有 web、
六个 worker、beat 都显式覆盖 `REDIS_CACHE_URL` 与 `REDIS_CHANNEL_URL`；只改 web
会形成两个锁命名空间，因此禁止逐个滚动切换。原 `REDIS_BROKER_URL`、
`REDIS_RESULT_URL`、`CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND` 保持现值。

默认 runtime 数据集上限 256mb，容器上限 384m、预留 128m；web 默认上限 768m。
已有 `.env.docker` 的 `WEB_MEMORY_LIMIT` 仍优先，如需提高旧部署，应明确设置
`WEB_MEMORY_LIMIT=768m`。以下变量可调整：

| 变量 | 默认值 |
|---|---|
| REDIS_RUNTIME_MAXMEMORY | 256mb |
| REDIS_RUNTIME_MEMORY_LIMIT | 384m |
| REDIS_RUNTIME_MEMORY_RESERVATION | 128m |
| REDIS_RUNTIME_CACHE_URL | redis://redis_runtime:6379/2 |
| REDIS_RUNTIME_CHANNEL_URL | redis://redis_runtime:6379/1 |

两实例默认共用 `REDIS_PASSWORD`，无需把密码写入覆盖文件。没有公共端口和数据
持久卷，关闭 AOF/RDB，采用 `noeviction`。数据集上限应小于容器上限，余量用于
连接缓冲与内存分配开销。`noeviction` 会在内存满时拒绝写入，不会静默删除锁；
必须监控 used_memory/maxmemory、拒绝写入、连接数及整机内存，接近上限时调整预算。
容器上限不是整机同时可用内存承诺，扩容前还需计入 MySQL 和所有 prefork 子进程峰值。

## 哪些数据不能丢

默认 Django session 使用数据库；`UserActiveSession` 也是数据库权威记录
（`accounts/utils.py::_sync_active_session_state`），cache 中的映射是镜像。
但 cache DB2 还保存任务互斥锁、登录锁、WebSocket 容量与 worker lease、限流、
在线状态等，不能当作可任意清空的页面缓存。DB1 存有带 TTL 的 Channels 消息
和群组成员，迁移也应保留。正式环境若覆盖 SESSION_ENGINE，还须核查该部署的
会话存储，不应假设一定是数据库。

runtime 重启会丢失数据；`noeviction` 不能防止重启丢锁。计划重启须先冻结所有
写方并让在途任务结束。意外重启应作为协调状态丢失事件处理：暂停新入口、检查
在途任务、锁及容量计数，再恢复。不能仅因 PING 成功就认定业务协调状态已恢复。
该配置降低 AOF 耦合，不代替数据库事务、任务幂等和主机磁盘问题治理。

## 迁移前审查

1. 在发布机使用显式 `--env-file ".env.docker"` 验证合并配置，检查所有应用的
   两个 runtime URL 一致、broker/result 不变、密码继承正确。不要把完整 config
   输出发到工单或日志，它可能包含 env_file 中的秘密。
2. 确认 Redis 7+、两实例时间差不超过 100ms、目标 DB1/DB2 都为空；若不是默认
   DB1/DB2 布局，先调整方案，本工具只迁移这两库。不要复制 broker DB0。
3. 启动新 runtime 服务并验证内存、`noeviction`、AOF/RDB 关闭。仅启动它不会
   改变应用连接地址。按当前实例 DB1/DB2 数据量确认内存余量。
4. 将 `SOURCE_RUNTIME_REDIS_URL` 和 `TARGET_RUNTIME_REDIS_URL` 通过受控环境变量
   注入一次性工具容器，必须含所需认证。不要把 URL 作为命令行参数，也不要打印
   环境变量。使用审阅后的新镜像，其中包含迁移脚本。

```sh
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" -f "docker-compose.redis-runtime.yml" config --quiet
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" -f "docker-compose.redis-runtime.yml" up -d --no-deps redis_runtime
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" -f "docker-compose.redis-runtime.yml" run --rm --no-deps --entrypoint python -e SOURCE_RUNTIME_REDIS_URL -e TARGET_RUNTIME_REDIS_URL web scripts/migrate_runtime_redis.py
```

若有额外镜像覆盖或入口网络配置，每条命令保留相同的 `-f` 文件集合与顺序。
上述工具命令默认只读，不修改源或目标。

## 冻结、复制与统一切换

先阻止公网写入和新 WebSocket，暂停 beat/其他发布者，温停 web 和全部 worker；
检查外部管理脚本、旧容器副本、其他主机及自动重启控制器也不能继续写缓存或
Channels。确认没有在途任务、保留未消费的 broker 队列。不要通过短超时强杀
长任务来完成这个步骤；需要明确的维护窗口与实际进程检查。

旧 `redis` 与新 `redis_runtime` 在迁移期间继续运行，所有应用保持停止。随后：

```sh
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" -f "docker-compose.redis-runtime.yml" run --rm --no-deps --entrypoint python -e SOURCE_RUNTIME_REDIS_URL -e TARGET_RUNTIME_REDIS_URL web scripts/migrate_runtime_redis.py --apply --writers-stopped
```

`--writers-stopped` 是运维方对真实停写状态的声明，不是脚本替代运维冻结的能力。
工具要求两个目标 DB 全空；使用 Lua 原子读取 DUMP/类型/绝对到期时间，二进制
RESTORE 且不 REPLACE，不延长锁或消息寿命，不改变永久键。Redis hash/set 的
内部哈希布局可能跨实例变化，验证使用排序后的二进制内容散列、类型与绝对 TTL，
而不是误把不同 DUMP 编码当作内容改变。支持 string/list/hash/set/zset；遇到其它
类型中止并审查，不能静默漏数据。

工具完成两库复制后复查源数据与目标数据，检测复制期间的写入；这仍不能替代
停止所有写方。失败时只清理本次已确认创建、且值及 TTL 仍属于本次复制的键。
如果丢失 RESTORE 响应、网络故障或目标被其他写方修改，可能报告清理不完整，
此时不要启动应用，更不要自动 FLUSHDB。源始终不被工具修改，便于重新审查。

输出只有数据库编号、数量、类型统计、绝对到期时间范围和内容/TTL SHA256，
不输出键名、payload 或认证。保存此审计结果，确认两库对应散列与统计一致，再
使用相同覆盖文件整体恢复 worker、web、beat。最终检查登录/单会话、未读邮件、
聊天、在线人数、队列执行探针与在途任务；确认没有应用仍连接旧 DB1/DB2 后才
放行用户入口。旧 DB1/DB2 留存并自然过期，不 FLUSH；原 broker 不重建、不清队列。

## 回滚边界

在恢复任何写方之前，可以撤去 runtime override 并整体恢复旧 URL，源数据未被
迁移工具修改且 TTL 未延长。恢复流量以后，旧库已经过时，禁止直接切回旧缓存。
代码回滚应保留当前 runtime URL；如果必须回滚 Redis 路由，重新冻结全写方，
将当前 DB1/DB2 按相同规则复制到全新的空目标实例并统一切换。不能把当前锁状态
与旧会话镜像混合，也不能把旧快照当作最新数据恢复。

## 隔离验证

```sh
python -m pytest tests/test_runtime_redis_configuration.py -q
RUN_RUNTIME_REDIS_MIGRATION_TESTS=1 python -m pytest tests/test_runtime_redis_migration.py -q
```

迁移测试只启动随机 loopback 端口的私有 Redis，包括默认只读、二进制与绝对 TTL、
大型 hash/set、目标非空拒绝、停写声明、跨库并发变化检测、失败清理、丢失响应
及短 TTL 不复活；不会接触生产 Redis。
