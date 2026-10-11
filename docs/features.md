# 功能实现细节

更新：2026-10-11。接口总表见 [API](api.md)，页面实现见前端仓库 docs/features.md。

## 注册和邮箱验证码

代码：apps/user/views.py、serializers.py、models.py、throttles.py。

1. send-register-code 校验邮箱尚未占用；生成 6 位数字验证码，purpose=register，写入数据库后通过 Django 邮件后端发送。
2. 邮件发送异常或服务未接受发送时删除该记录并返回 503；SMTP 超时 15 秒。接口发送成功表示服务接受，实际到达仍需检查收件箱。
3. 注册输入 student_id/name/email/password/password_confirm/code，可选 class_name/phone/wechat_id/role。只允许 student 或 teacher，默认 student。当前教师自选注册，没有人工身份审批。
4. 验证码有效期 5 分钟、一次消费。注册 serializer 在事务内锁定该邮箱用途的验证码行，按 pk 排序，再校验过期/消费及邮箱占用；User 与 Profile 在同一事务创建，成功后标记验证码已用。邮箱占用检查忽略大小写。
5. 新用户内部 username 初始为 student_id；Profile.student_id 唯一。密码至少 6 字符、两次一致，使用 Django 哈希，不存明文。

改邮箱使用 purpose=change_email，注册验证码不能混用。提供 clear_expired_codes --minutes 5 手动清理命令，目前没有自动调度；维护时应考虑保留期限和隐私。

## 登录、独立管理员入口与多身份

代码：apps/user/authentication.py、roles.py、permissions.py、views.py；apps/management/views.py。

authenticate_login_id 优先查当前 Profile.student_id，再按对应内部 username 认证；无对应 Profile 时兼容已有 username。因此管理员修改学号/工号后新编号可登录，原内部用户名兼容仍保留。

普通登录可传 role；账号没有所选身份返回 403。管理员登录还要求基础 Profile.is_admin()，并将当前身份设为 admin。管理员页面独立在 `/admin/login`，普通登录页不显示后台入口；普通账号即使知道地址也不能访问管理数据。

remember_me=True 会话 14 天，否则浏览器关闭时过期。退出删除当前会话。身份切换通过退出、在对应入口重新登录；禁止通过改 Profile 字段授予自己新角色。多身份标记仅由服务器明确授权。

限流：匿名默认 100/hour、用户默认 200/hour；登录独立按 IP 5/minute，验证码按用户或 IP 5/minute。当前默认缓存是进程内缓存，多个 Gunicorn worker 的限流不是统一分布式额度；升级规模时应配置共享缓存并重新验证。

## 个人中心

代码：UserProfileSerializer、MeProfileView、AvatarUploadView、ChangePassword/ChangeEmail serializers。

可改姓名、班级/部门、电话、微信、简介。学号、角色、邮箱、头像字段不可经普通资料 PATCH 更改；邮箱和头像走专用接口。资料响应 role 是当前会话身份。

头像 PUT multipart，Pillow ImageField 验证图片，支持 JPEG/PNG/WebP、上限 5 MiB；返回公开头像 URL。头像是公开静态资源，不按私有简历策略访问。

改密码需要正确旧密码、新密码至少 6 字符且确认一致；视图 update_session_auth_hash 保持当前会话。邮箱变更给新邮箱发送验证码，拒绝已占用邮箱，在事务内锁验证码并重新检查邮箱，再修改 User.email。没有忘记密码流程。

## 项目状态与教师管理

代码：apps/event/models.py、views.py、serializers.py。

数据库 status 只有 draft/published/closed。API status 来自 get_runtime_status()：已发布且未到 start_time → published；已开始且未结束/无结束时间 → ongoing；结束后 → closed。无需 cron 更新数据库状态；筛选和统计必须复用时间条件，不能对数据库 status 查询 ongoing。

教师只管理自己的项目；当前 admin 通过教师接口可查看全站。创建为草稿，发布仅草稿；教师删除仅草稿，教师手动关闭仅已发布且未开始。管理员专用接口可以关闭任意尚未显式关闭的项目，并删除任意状态项目及其申请，响应包含 applications_deleted；这是管理员与教师权限的区别。

标题/类型/简介、时间、地点、容量、attachment_id 由 EventWriteSerializer 校验。结束时间必须晚于开始；容量至少 1 且不小于已通过人数。关闭项目不能编辑。附件必须 event_attachment，教师只能绑定自己上传的文件，当前 admin 可绑定其他上传者的此类文件。

公开项目列表/非草稿详情 API 不要求登录；草稿详情仅发布者或当前 admin 可看。前端项目广场仍要求学生身份登录。

当前 can_recruit=true 筛选只返回未开始且有名额项目，尽管 ongoing 允许报名；筛选语义尚未同步为“所有可报名项目”。维护时不得误写成它已经涵盖中途报名。

## 学生报名与审批

代码：EventApplicationCreateSerializer、StudentApplicationListCreateView、TeacherApplicationApprove/RejectView、lock_application_for_review。

可报名 published/ongoing 且有名额的项目。pending/approved 重复申请被拒绝；rejected 可再申请，历史申请保留。后端允许空 statement，当前页面要求填写陈述；可选 resume_id 必须本人上传且分类 application_resume。创建申请先锁项目，再校验重复和容量；pending 不占用名额，只有 approved 才增加 current_participants。

学生列表/详情仅自己的申请；教师仅所属项目申请；当前 admin 全站。审批按 Event → EventApplication 顺序锁行后重新判断，资源并发删除返回 404。审批意见可选。

| 运行状态 | 审核规则 |
|---|---|
| published | 允许通过/拒绝，也允许更改已有通过或拒绝结果；cancelled 不允许 |
| ongoing | 仅 pending 可首次通过或拒绝；已完成决定不可改 |
| draft/closed | 不可审批 |

通过前再次检查容量；新增通过人数 +1；重复通过在允许改判阶段不重复加人。已通过改拒绝人数 -1；拒绝不占名额。人数和决定在同一事务提交，管理员审批复用同一实现。没有申请撤回接口，cancelled 仅保留模型状态。

## 文件上传、访问与资料库

代码：apps/file/models.py、serializers.py、views.py；访问入口 get_accessible_file_queryset。

上传先 POST multipart 获取 UUID，再由项目/申请/模板接口绑定。磁盘路径 managed-files/<category>/<uuid>.<ext>；响应 file_url 是相对的受保护下载地址。前端上传期间禁止提交业务表单，失败不能绑定未完成文件。

| 分类 | 扩展名 | 上限 |
|---|---|---|
| event_attachment | zip/rar/7z/pdf/doc/docx | 100 MiB |
| application_resume | pdf/doc/docx/zip | 20 MiB |
| template | pdf/doc/docx/zip | 20 MiB |
| other | zip/rar/7z/pdf/doc/docx/txt/md/png/jpg/jpeg | 50 MiB |

一般上传校验扩展名和大小，未实现病毒扫描或所有格式的内容验证。上传接口要求登录，当前不按角色限制分类；实际业务绑定额外校验分类/所有权。

下载要求登录。当前 admin 可访问所有文件；其他账号可访问本人上传、启用模板、本人发布项目附件、数据库 published/closed 项目附件、本人申请简历、本人项目收到的简历。其他私有简历返回 404。停用模板的文件对普通用户不再因模板引用获得访问，但上传者及另有合法引用的账号仍能访问。

文件被任何模板（含停用模板）、项目或申请引用时不可删除。可删除文件会移除数据库和物理存储文件。上传后取消业务提交可能留下未引用文件；通过文件管理清理，当前没有自动清理任务。

知识库是 FileTemplate 文件资料库。管理员使用 key/name/description/file_id/is_active 发布，key 为唯一 slug；必须绑定 template 分类文件。普通列表仅 is_active=True。DELETE 是软停用，返回 200 及记录；PATCH is_active=True 可恢复。替换模板文件不自动删除旧文件。FileAuditLog 记录上传/下载/删除和模板操作；管理员专用文件删除接口目前没有复用普通文件视图的审计写入。

## 管理员管理

代码：apps/management/views.py、serializers.py；每个管理数据接口都要求当前 IsAdmin。

仪表盘返回账号按基础角色分布、项目按运行状态分布、申请状态和文件数量。用户管理支持搜索、角色过滤、创建、编辑、禁用/恢复和删除。不能删除自己或改变自己角色；密码留空不变，非空至少 6 字符。创建需 username 与 student_id，二者可以不同；邮箱忽略大小写防重复。

删除学生在事务内按 pk 顺序锁定关联项目，统计其 approved 数量，级联删除账号/申请，再释放对应名额，不影响其他学生。删除教师会级联其发布项目和相关申请，不能当作单纯“隐藏账号”处理。文件上传者字段会置空，物理文件不因账号删除自动清理。

资料模板管理位于 `/api/file/admin/templates/`，不是 `/api/admin/templates/`。应用 admin 与 Django 原生后台分开；不要为应用管理员随意授予 is_superuser。

## 通知

代码：apps/notification/models.py、services.py、views.py；由 event 视图调用生成服务。

ENABLE_NOTIFICATIONS=True 时，提交申请通知发布者，通过/拒绝通知学生；记录与事件操作处于同一事务。列表、详情、标记单条/全部已读仅当前接收人可访问，支持 is_read 筛选。它是数据库站内通知，不是 SMTP 邮件；前端尚无通知列表或铃铛。
