# 自强科协网站后端

Django / Django REST Framework 提供账号、项目报名审批、文件资料、通知和管理员 API。前端仓库：[ZiqiangFront](https://github.com/honghuwu/ZiqiangFront)。正式网站：https://ziqiangkexie.cn/；管理员独立登录 `/admin/login`。

## 维护文档

| 文档 | 阅读目的 |
|---|---|
| [架构与数据模型](docs/architecture.md) | 模块职责、调用链、数据库关系、会话和权限 |
| [功能实现细节](docs/features.md) | 每项功能的规则、代码位置、事务与边界 |
| [API 契约](docs/api.md) | 接口、方法、输入和响应约定 |
| [部署与恢复](docs/deployment.md) | 本地启动、生产配置、发布、备份、回滚、故障定位 |
| [验证与维护流程](docs/maintenance.md) | 测试覆盖、已知缺口、每次修改如何更新文档和 GitHub |
| [更新记录](CHANGELOG.md) | 功能行为变化及验证结果 |
| [全站联调报告](qa/full-site-qa-20261011.md) | 2026-10-11 的实际检查范围和证据 |

初次维护先读架构和功能，再按 API 与前端页面对照。旧入口 [backend-dev.md](backend-dev.md) 保留为导航。

## 本地启动

Python 3.11–3.13；生产 PostgreSQL 15，本地可用 SQLite。依赖版本以 requirements.txt 为准。

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# 编辑 .env：使用自己的密钥，开发邮件可用 console backend
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

本仓库不包含生产 .env、数据库、用户上传文件、测试账号密码或证书私钥。
