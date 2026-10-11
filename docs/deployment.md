# 部署、备份与恢复

更新：2026-10-11。本文描述当前服务器布局；凭据在服务器私有配置，不能从 GitHub 获取。

## 环境与配置

当前 Debian 12、Python 3.11、PostgreSQL 15、Nginx（已有宝塔安装）、Gunicorn 两个 sync worker。代码 `/opt/ZQbackend`，虚拟环境 `/opt/ZQbackend/venv`，前端静态产物 `/opt/ZQfrontend/dist`。正式域名 ziqiangkexie.cn / www.ziqiangkexie.cn；应用管理员 `/admin/login`，原生 Django `/django-admin/`。

后端 .env 用 .env.production.example 的占位符自行填写。关键变量：DJANGO_ENV=production、DEBUG=False、DEBUG_LOG_API=False、DJANGO_SECRET_KEY、DJANGO_ALLOWED_HOSTS、DB_ENGINE=postgresql、DB_NAME/USER/PASSWORD/HOST/PORT、CSRF_TRUSTED_ORIGINS、邮件配置、通知开关。生产 Session/CSRF Cookie Secure+HttpOnly+SameSite=Lax；HTTPS 代理头和重定向已配置。

配置 .env/production.env 应仅 root 和应用组可读（例如 640）；代码目录 755、源码 644、root 所有，www-data 可写 media/logs/.cache。备份与密钥 root 私有。不要将本机 .env、SQLite、venv、node_modules、用户文件上传进代码仓库或覆盖生产配置。

## 初次部署

1. 创建 PostgreSQL 专用数据库/用户，Gunicorn 不使用数据库超级用户。生产只监听本地 PostgreSQL 和 Gunicorn 127.0.0.1:8000。
2. 安装后端 requirements，migrate、collectstatic。通过私有环境配置 ADMIN_USERNAME/PASSWORD/EMAIL 后按需执行 createsuperadmin；该命令会重置已有指定账号的密码并设置应用管理员（不会授予 Django staff/superuser），不应每次发布重复运行。
3. 前端使用 Node ^22.12 或 >=24，npm ci 后 npm run build。
4. 安装 deploy/ziqiang.service、ziqiang-backup.service/timer，启用服务。Nginx 实际配置位于 `/www/server/panel/vhost/nginx/ziqiang.conf`；先 nginx -t 再 reload。
5. DNS 根域与 www 指向当前服务器；主机商过白须覆盖服务器实际线路。ICP备案/接入状态应另行核验，2026-10-11 未独立确认备案号。
6. HTTPS 签发前使用 nginx.awaiting-dns.conf 保留 ACME HTTP 校验。activate-https.sh 取得根域/www 证书、备份并替换 Nginx 配置、安装续期重载钩子、启用 certbot.timer。脚本含 --agree-tos 和无邮件注册，操作者需了解该签发选项；更换域名/配置路径要先改脚本。nginx.preview.conf 是私有预览模板，不作为公网生产配置。

## 每次发布

先完成相关代码测试、文档、提交和 GitHub 推送，记录后端/前端提交 SHA。备份数据库、media、环境及正在使用的两端产物；数据库迁移前备份尤其必要。

更新仅代码，保留生产环境文件/虚拟环境/media。源码变更如依赖变化则安装 requirements；执行：

```sh
cd /opt/ZQbackend
venv/bin/python manage.py migrate --noinput
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py check --deploy
systemctl restart ziqiang
systemctl status ziqiang --no-pager
```

Gunicorn preload_app=True，更新后需重启加载。纯前端修改无需重启后端。构建成功后同步 dist，先上传资产再切换 index 或采用新发布目录原子切换，避免用户取到尚不存在的 hashed 资源；旧 hashed 资产宜暂留，避免已打开页面的懒加载失败。当前部署采用直接目录同步，尚未配置版本目录原子切换和 CI/CD。

macOS openrsync 同步可用 --chmod=u=rwX,go=rX；某些 rsync 支持 D755/F644 简写但 macOS 不支持。部署后显式修正 root 所有权、目录 755、源码/前端资源 644，并以 www-data 检查可读。不要把本机 700 目录带到服务器；不能只凭 restart 后短暂 active 判定成功。

发布后检查根域/www HTTPS、HTTP→HTTPS、刷新深层路由、CSRF、三角色登录和所改业务。部署检查目前保留 HSTS include_subdomains/preload 两项 warning，未开启它们；新增子域全部 HTTPS 确认后再决定。HSTS 默认 3600 秒。

## 备份与恢复

backup.sh 以 root 执行、umask 077，生成 `/root/ziqiang-backups/database-<time>.dump`、media tar、config tar。systemd timer 每天服务器时区 03:15；当前时区 Asia/Shanghai。config 包含私有 .env，不能公开。脚本不自动备份前端或代码版本，发布前另行保存。没有自动轮换/异地备份，需规划磁盘和保留期。

```sh
systemctl start ziqiang-backup
systemctl list-timers ziqiang-backup.timer certbot.timer
```

回滚顺序：停止写入/服务 → 恢复匹配代码与前端版本 → 判断迁移是否向后兼容 → 必要时恢复部署前数据库和匹配 media/config → migrate/check/启动 → 验证业务。只回滚源码不能撤销数据库变化；恢复旧备份会丢失备份之后写入，必须明确确认恢复范围。

数据库恢复先在独立临时库演练，例如以 postgres 运行 createdb ziqiang_restore_check，再 pg_restore --no-owner --no-acl -d ziqiang_restore_check <backup.dump>；核验表和记录后再安排正式恢复。不要直接对生产库试验 --clean。媒体恢复目录所有权需要 www-data 可写。2026-10-11 已做一次备份恢复演练，但定期演练仍由维护人安排。

## HTTPS 与故障定位

certbot.timer 已启用，deploy hook 执行 nginx -t && nginx -s reload。续期检查可运行 certbot renew --dry-run，证书应覆盖两个正式域名。不要仅依赖文档中历史到期日期。

| 现象 | 优先检查 |
|---|---|
| 主机商拦截页 | DNS、服务器线路匹配的过白、备案/接入和客服记录 |
| 502/服务重启循环 | journalctl -u ziqiang；Gunicorn 错误日志、代码读权限、数据库连接 |
| CSRF 403 | token 是否登录后轮换、Origin/Host、CSRF_TRUSTED_ORIGINS、HTTPS 代理头 |
| 验证码 503 | SMTP/TLS/授权码/发信服务；日志不打印验证码或授权码 |
| 上传 413 | Nginx client_max_body_size=105M 及对应分类上限 |
| 文件 404 | 文件权限、模板停用/引用关系、磁盘是否存在；私有 media 直链本来就是 404 |
| 深层路由刷新 404 | Nginx try_files 回退 index.html 和实际 vhost |
| 429 | 登录/验证码窗口、共享出口 IP、缓存策略；测试时不得降低生产限流 |

Nginx 日志 `/www/wwwlogs/ziqiang-access.log`、`ziqiang-error.log`，后端 logs/gunicorn-error.log/access.log。clear_expired_codes --minutes 5 可手动清旧验证码，当前没有自动调度。
