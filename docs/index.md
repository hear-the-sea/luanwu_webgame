# 春秋乱世庄园主 - 技术文档

这里只列仍作为开发、联调与运维依据的文档。已经失去事实依据的设计稿和资料稿已从主文档集中移除。

带日期的审计、兼容清单和优化计划属于“阶段性快照”，不是常驻架构规范；需要判断当前系统事实时，优先看 README、架构、开发、数据库、配置数据和运行手册。

## 核心文档

| 文档 | 说明 | 适用读者 |
|------|------|----------|
| [架构设计](architecture.md) | 当前系统分层、关键依赖、部署形态与测试门禁 | 后端开发、维护者 |
| [开发指南](development.md) | 本地开发、Docker、测试、调试命令 | 所有开发者 |
| [接口与实时入口](api.md) | HTTP 页面路由、JSON 端点、WebSocket 入口与限流边界 | 前端、测试、联调 |
| [数据库边界](database.md) | 当前数据库角色、模型归属、迁移与索引协作约束 | 后端开发、DBA |
| [配置数据](config_data.md) | `data/*.yaml` 的职责、刷新方式与部署注意事项 | 开发、测试、运维 |
| [编码规范](coding_standards.md) | 代码风格与导入约定 | 所有开发者 |

## 运维与治理

| 文档 | 说明 |
|------|------|
| [健康检查运行手册](runbook_health_checks.md) | `/health/live` 与 `/health/ready` 的排障口径 |
| [Docker 部署运行手册](runbook_deploy_docker.md) | 镜像构建、传输、更新容器、Redis 认证与清理经验 |
| [数据流边界](domain_boundaries.md) | 关键领域的数据来源、缓存、补偿与失败语义 |
| [第二阶段统一写模型基线](write_model_boundaries.md) | `mission / raid / guest recruitment` 写路径基线 |
| [技术审计（2026-03）](technical_audit_2026-03.md) | 当前治理基线、约束与验证记录 |
| [优化计划](optimization_plan.md) | 与技术审计配套的执行路线图 |
| [兼容入口清单（2026-03）](compatibility_inventory_2026-03.md) | 当前仍明确保留的兼容入口 |

## 工具与补充

| 文档 | 说明 |
|------|------|
| [战斗调试器网页指南](../battle_debugger/WEB_GUIDE.md) | battle debugger 的启用条件与页面用法 |

## 快速导航

1. 新同学先看 [README](../README.md)、[开发指南](development.md)、[架构设计](architecture.md)
2. 联调页面动作、JSON 端点或 WebSocket，先看 [接口与实时入口](api.md)
3. 改模型、迁移、索引或并发状态机，先看 [数据库边界](database.md) 与 [数据流边界](domain_boundaries.md)
4. 涉及 YAML、导库或热刷新，先看 [配置数据](config_data.md)
5. 涉及镜像构建、服务器更新、Redis 认证或 Docker 清理，先看 [Docker 部署运行手册](runbook_deploy_docker.md)

*最近校正：2026-04-06*
