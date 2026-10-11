# 后端开发指南

维护说明已整理到 docs/，以当前实现为准，避免把早期联调记录当成现行规则。

- [架构与数据模型](docs/architecture.md)
- [各功能的实现细节](docs/features.md)
- [API 契约](docs/api.md)
- [部署、备份与回滚](docs/deployment.md)
- [验证与 GitHub 维护流程](docs/maintenance.md)
- [2026-10-11 全站联调报告](qa/full-site-qa-20261011.md)

最终完整回归为 PostgreSQL 90 项通过；曾出现的 76/83 项为同一天较早阶段结果。管理员独立入口 `/admin/login`，普通登录页不展示管理员链接；进行中项目允许中途报名并审核新的待审申请。
