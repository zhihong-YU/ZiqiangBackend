# 架构与数据模型

更新：2026-10-11。

## 运行链路

浏览器 React → 同域 Nginx `/api/` → Gunicorn `core.wsgi` → Django/DRF → PostgreSQL。

Nginx 提供前端 dist、Django staticfiles 和公开头像。其他媒体不能通过 `/media/` 直接访问，受保护下载由 Django 校验会话和资源权限。开发时 Vite 将 `/api/` 和 `/media/avatars/` 代理到 127.0.0.1:8000；生产不使用 Vite。

## 源码职责

| 目录/文件 | 职责 |
|---|---|
| core/settings.py、urls.py | 环境、数据库、认证、分页、限流、URL 挂载 |
| core/error_codes.py | 业务错误常量、DRF 异常响应封装 |
| core/pagination.py | 默认 20 条，page_size 上限 100 |
| core/middleware.py | 可选 API 调试日志；生产必须关闭 DEBUG_LOG_API |
| apps/user | 注册验证码、登录、资料、头像、密码和邮箱变更、当前身份 |
| apps/event | 项目状态、报名、审批、人数统计和行锁 |
| apps/file | 上传校验、受保护文件下载、资料库、文件审计 |
| apps/management | 应用管理员登录、全站统计、用户/项目/申请/文件管理 |
| apps/notification | 站内通知模型、生成服务和本人通知接口 |
| deploy | Gunicorn、Nginx、systemd、HTTPS 和备份脚本 |
| qa | 带日期的实际联调结果，不存测试凭据 |

各 app 的 urls.py → views.py → serializers.py / models.py；业务范围由视图权限和 queryset 限制，字段输入由 serializer 校验。

## 数据关系

| 模型 | 重要字段与关系 |
|---|---|
| Django User | 内部 username、邮箱、哈希密码、is_active；不是应用 role |
| UserProfile | 一对一 User；唯一 student_id；姓名、班级/部门、联系方式、简介、头像、基础 role、can_switch_roles |
| EmailVerificationCode | 邮箱、6 位验证码、用途 register/change_email、创建时间、is_used |
| Event | 发布者 User、时间、容量/已通过人数、数据库状态、可选 ManagedFile 附件 |
| EventApplication | Event + 学生 User、陈述、可选简历、审批状态、意见、审批时间 |
| ManagedFile | UUID、磁盘文件、原名、分类、大小、上传者；上传者删除后 SET_NULL |
| FileTemplate | 唯一 slug key、名称/说明、ManagedFile、is_active；file 使用 PROTECT |
| FileAuditLog | 操作者、操作类型、关联文件、模板键、请求 IP/UA、元数据 |
| Notification | 接收人、类型、标题/内容、已读状态、关联业务元数据 |

删除 User 会级联删除其 Profile、发布项目、申请和接收通知。删除项目会级联删除项目申请；不会自动删除 ManagedFile 的物理文件。删除已通过学生时必须先释放关联项目人数，见功能说明。

## 身份与安全边界

应用角色 student/teacher/admin 与 Django is_staff/is_superuser 分开。应用管理员不一定能进入 `/django-admin/`。初始化命令 createsuperadmin 的行为见部署文档。

使用 Django SessionAuthentication；Cookie 是身份凭据，前端不存 JWT。普通/管理员登录都在视图主动 enforce_csrf，不能因 URL 中 csrf_exempt 误认为允许无 CSRF 登录。前端先获取 `/api/user/csrf/` 的 JSON token，再带 X-CSRFToken 提交写请求。

基础角色保存在 UserProfile.role；登录后选定身份保存在 session.active_role。roles.py 每次校验当前身份是否仍在 available_roles 中。仅基础 admin 且 can_switch_roles=True 的明确授权账号拥有三种选择；公开注册、资料更新和普通管理表单均不授予该标记。以学生/教师身份进入时没有管理员越权豁免。同一浏览器共享会话，同时只能使用一种身份。

前端路由守卫提供页面约束；真正的数据边界在后端权限类和 queryset。文件访问除当前 admin 外按上传者及业务引用关系判定，不以 UUID 难猜作为权限保护。

## 迁移与并发

迁移必须提交到 Git。0008 新增 can_switch_roles，默认 False，不会给已有普通账号授予多身份。

PostgreSQL 是生产语义基准。申请、审核、验证码消费、删除账号使用 transaction.atomic / select_for_update；SQLite 不提供等价行锁。锁顺序为先项目再申请，账号删除按项目 pk 顺序取锁，避免与审批倒序锁定。
