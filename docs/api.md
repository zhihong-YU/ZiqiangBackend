# API 契约

更新：2026-10-11。此表对照各 app 的 urls.py、views.py、serializers.py；业务约束见 [features.md](features.md)。

## 通用约定

URL 保留尾斜杠。JSON 请求 Content-Type: application/json；文件/头像使用 multipart/form-data，由浏览器生成 boundary。输入外键使用 *_id，输出提供嵌套对象及受保护的相对 file_url。

采用 Session Cookie。同域写请求 POST/PUT/PATCH/DELETE 带 X-CSRFToken，从 GET `/api/user/csrf/` 返回的 csrfToken 获取；管理员也可 GET `/api/admin/csrf/`。登录成功会轮换 CSRF token，不能一直复用旧 token。

分页列表响应 `{count,next,previous,results}`，参数 page（从 1 开始）、page_size（默认 20，最大 100）。前端需读 results；按完整集合分组的页面读取 next，管理大列表使用服务端分页。

常见状态：200 查询/更新、201 创建、204 无响应体删除、400 参数或业务校验、403 会话/权限/CSRF、404 不存在或不可见、429 限流、503 发验证码失败。SessionAuthentication 下匿名请求常为 403，不能只检查 401。业务 error_response 通常 `{detail,error_code}`；serializer 错误可能是字段数组或 detail 数组，附 VAL_008。错误常量表不能保证每个视图都使用专门业务码；很多校验目前统一 VAL_008。NotFound 统一附 EVENT_006，包括非项目资源。

## 用户 `/api/user/`

| 方法/相对路径 | 输入或返回 | 权限 |
|---|---|---|
| GET csrf/ | `{csrfToken}` | 公开 |
| POST send-register-code/ | email；成功 detail | 公开，验证码限流 |
| POST register/ | student_id,name,email,password,password_confirm,code；可选 role,class_name,phone,wechat_id | 公开，只能 student/teacher |
| POST login/ | student_id,password；可选 role,remember_me | 公开，手动校验 CSRF 和登录限流 |
| POST logout/ | 无业务字段；detail | 已登录 |
| GET me/profile/ | id,name,student_id,class_name,phone,wechat_id,bio,role,email,avatar,avatar_url | 本人 |
| PUT/PATCH me/profile/ | name,class_name,phone,wechat_id,bio | 本人；其他身份字段只读 |
| PUT me/avatar/ | multipart avatar | 本人 |
| POST change-password/ | old_password,new_password,new_password_confirm | 本人 |
| POST send-change-email-code/ | email | 本人，验证码限流 |
| POST change-email/ | email,code | 本人 |

注册示例（均为虚构值，code 需实际获取）：

```json
{"student_id":"sample001","name":"测试学生","email":"sample@example.com","password":"example-password","password_confirm":"example-password","code":"123456","role":"student","class_name":"示例班级"}
```

## 项目与申请 `/api/event/`

| 方法/相对路径 | 功能 | 权限 |
|---|---|---|
| GET events/ | 非草稿列表 | 公开 |
| GET events/<id>/ | 项目详情，草稿受限 | 非草稿公开，草稿发布者/admin |
| GET/POST teacher/events/ | 列表/创建草稿 | teacher 自己，当前 admin 全站 |
| GET/PUT/PATCH/DELETE teacher/events/<id>/ | 详情/编辑/删除草稿 | teacher 自己，当前 admin 可越过所有权 |
| POST teacher/events/<id>/publish/ | 草稿发布 | 同上 |
| POST teacher/events/<id>/close/ | 未开始的已发布项目关闭 | 同上 |
| POST events/<id>/apply/ | statement、可选 resume_id | 当前 student |
| GET my-applications/ | 本人申请 | 当前 student |
| GET my-applications/<id>/ | 本人申请详情 | 当前 student |
| GET teacher/applications/ | 收到的申请 | teacher 自己项目/admin 全站 |
| POST teacher/applications/<id>/approve/ | 可选 review_note | 同上 |
| POST teacher/applications/<id>/reject/ | 可选 review_note | 同上 |

项目写入字段：title,event_type,start_time,end_time（可空）,location,description,expected_participants,attachment_id（可空）。时间为带时区 ISO 8601；数据库状态和 current_participants 不允许普通请求直接指定。

项目返回字段：id,title,event_type,start_time,end_time,location,description,attachment,expected_participants,current_participants,status,teacher,teacher_name,can_delete,can_publish,can_close,pending_applications_count,created_at,updated_at。status 是运行状态。attachment/resume 为文件对象或 null。

项目列表筛选 teacher_name,status,can_recruit；管理项目另支持 search（见管理接口）。can_recruit 当前含义为“未开始且有剩余名额”，不包含 ongoing。申请列表筛选 event/status；申请返回 id,event,event_title,student,student_name,student_id,student_email,statement,resume,status,review_note,reviewed_at,created_at,updated_at。

审批允许进行中 pending 首次审核，但不允许进行中已完成结果改判；发布未开始阶段允许改判。重复通过不会重复计数。POST my-applications/ 不用于提交报名，需使用含 event id 的 apply 地址。

## 文件 `/api/file/`

| 方法/相对路径 | 功能/输入 | 权限 |
|---|---|---|
| POST uploads/ | multipart file,category,可选 description | 已登录 |
| GET my-files/ | 上传文件，category 筛选；admin 全部 | 已登录 |
| GET uploads/<uuid>/ | 元信息 | 有访问权 |
| DELETE uploads/<uuid>/ | 删除未引用文件，204 | 上传者/admin |
| GET uploads/<uuid>/download/ | 带原文件名的文件流 | 有访问权 |
| GET templates/ | 启用资料列表 | 已登录 |
| GET templates/<key>/ | 启用资料详情 | 已登录 |
| GET/POST admin/templates/ | 全资料/新建 | 当前 admin |
| GET/PUT/PATCH admin/templates/<key>/ | 详情/编辑/重新发布 | 当前 admin |
| DELETE admin/templates/<key>/ | 停用，200 返回记录 | 当前 admin |

ManagedFile 返回 id,original_name,category,content_type,file_size,description,file_url,is_referenced,can_delete,created_at,updated_at。文件访问权按资源引用判断，详见 features.md。

模板写入 key,name,description,file_id,is_active；key 为唯一 slug（字母/数字/下划线/连字符，最长 50），绑定 template 分类。模板返回 key,name,description,file,is_active,created_at,updated_at。管理员模板列表支持 is_active=true/false，普通列表不展示停用资料。

## 管理员 `/api/admin/`

管理数据接口均要求当前 admin；公开注册不授予管理员身份。

| 方法/相对路径 | 功能/输入 |
|---|---|
| GET csrf/ | 公开获取 token |
| POST login/ | student_id,password,可选 remember_me；基础角色须 admin |
| POST logout/ | 退出管理员会话 |
| GET dashboard/ | total_users/total_students/total_teachers/total_admins/total_events/events_by_status/total_applications/applications_by_status/total_files |
| GET users/ | role/search/page/page_size 筛选分页 |
| GET users/<id>/ | 用户详情 |
| POST users/create/ | username,email,password,name,student_id；可选 class_name,phone,wechat_id,bio,role |
| PUT users/<id>/update/ | email,name,student_id,role,is_active；可选 password,class_name,phone,wechat_id,bio |
| DELETE users/<id>/delete/ | 账号级联删除，释放其 approved 名额；204 |
| GET events/ | status/teacher_name/search/page/page_size |
| POST events/create/ | 项目字段 + teacher_id；创建草稿 |
| GET events/<id>/ | 详情 |
| PUT events/<id>/update/ | 项目字段 + teacher_id |
| DELETE events/<id>/ | 任意状态项目和申请删除，200 包含 applications_deleted |
| POST events/<id>/publish/ | 草稿发布 |
| POST events/<id>/close/ | 管理员强制关闭 |
| GET applications/ | event/status/page/page_size |
| POST applications/<id>/approve/ | 可选 review_note；复用教师审核约束 |
| POST applications/<id>/reject/ | 可选 review_note；复用教师审核约束 |
| GET files/ | category/search/page/page_size |
| DELETE files/<uuid>/ | 引用保护，无引用文件删除，204 |

用户更新为 PUT，按 serializer 必填字段提交完整表单；password 留空不改，非空至少 6 字符；不能删除自己或改变自己的基础角色。can_switch_roles 不在上述写入字段中。

## 通知 `/api/notification/`

| 方法/相对路径 | 功能 |
|---|---|
| GET my/ | 本人通知，is_read=true/false |
| GET my/<id>/ | 本人通知详情 |
| POST my/<id>/read/ | 单条已读 |
| POST my/read-all/ | 全部已读 |

均需登录且限本人。ENABLE_NOTIFICATIONS 控制业务是否生成新通知，已生成记录仍可查询；前端尚无通知页面。
