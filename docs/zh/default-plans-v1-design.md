# 新用户默认 quota 方案：共同接口合同 v1 核对与设计

日期：2026-10-31。范围：仅 TekesQuotaKit；SKZY admin/backend 由其他会话负责。
最终核对基线：`d03b7b7`（main，包元数据版本 `0.16.2`；不据此断言已发布）。
初始本地基线 `968577d / 0.13.3` 已在核对中更新。本批仅文档和合同核对，未实现、未发布、未部署。

## 结论与状态

用户提供的 v1 职责、URL、请求/成功响应和首次发放幂等语义**可实现，予以确认并保存**。
这不表示当前基线已支持这些接口，也不表示下文待确认补充已成为消费方合同。
现有代码不能直接拼接成满足 v1 的实现：需要独立初始化事实、同事务追加发放、
独立授权和实际参与计算的权益快照。不得将现有整表保存包接口当作赠送接口。

合同冻结前需确认四项最小补充：授权角色、包语义/有效期、分页和错误映射。
保留所有已给定字段和语义；以下建议均标记为“待确认”，不要求 SKZY 猜测适配。
不实施存在上述争议的运行时代码。Issue 链接及本批验证见文末。

## 保存的共同接口合同 v1

### 职责、认证与模型

- Quota 管理默认方案，一个方案包含多个有序 quota 包；管理端可创建、编辑、启停。
- SKZY admin 保存“小区 → default_plan_id”；Backend 首次成功绑定小区后请求发放。
- Quota 不存储或判断房间/小区归属；`estate_id` 仅保存为来源审计信息。
- 复用服务端调用方认证，凭据决定 tenant；请求体不能指定或覆盖 tenant。
- 目录只读和初始化写入分别授权，复用现有权限机制；凭据只放服务端。
- `subject_id` 沿用现有用户标识合同；方案 ID 为 Quota 生成的稳定、不透明字符串。
- 优先复用包/Level 模型，不建立第二套计算体系。
- 方案名称、内容、启用状态可改，ID 不变；已发放权益不因方案编辑变化。
- 实际发放保存方案版本或内容快照及各包身份，支持追溯。

### GET /v1/default-plans

返回凭据 tenant 的方案摘要，包含停用方案供 admin 显示已有配置；明确分页，不静默截断：

```json
{
  "items": [
    {
      "id": "稳定方案ID",
      "name": "方案名称",
      "enabled": true,
      "revision": 1,
      "packages": [{"code": "包标识", "name": "包名称"}]
    }
  ]
}
```

### POST /v1/subjects/{subject_id}/default-initialization

```json
{
  "default_plan_id": "方案ID",
  "source": {
    "system": "skzy",
    "event": "first_estate_binding",
    "estate_id": "服务端确认的小区ID",
    "binding_id": "稳定绑定记录ID"
  }
}
```

成功响应：

```json
{
  "subject_id": "用户ID",
  "initialization": {
    "id": "初始化记录ID",
    "default_plan_id": "实际首次发放方案ID",
    "plan_revision": 1,
    "status": "applied",
    "applied_at": "UTC时间",
    "package_ids": ["已发放包实例ID"]
  },
  "replayed": false
}
```

重复调用返回同一初始化事实，`replayed=true`。即使改传另一方案，仍返回首次事实，不再次赠送。
错误沿用现有外壳，至少区分方案不存在、方案停用、无权限、参数错误、服务故障。

### 发放、重试与保留

- 唯一业务边界为 `(tenant, subject_id, 默认初始化)`；不使用小区、绑定或方案作为领取边界。
- 所有包和初始化事实同事务提交；任何一步失败全部回滚。
- 并发、重复、重启、提交成功后响应丢失重试均不重复发放。
- 已有初始化先返回原结果，不因原方案停用或修改阻断重试。
- 不覆盖付费权益，不通过“替换全部用户包”赠送。
- 到期、撤销、换小区均不清除初始化事实。

## 当前实现证据与兼容矩阵

以下路径相对仓库根目录，核对对象均为上述基线源代码。

| 合同项 | 当前证据 | 结论/必要变更 |
| --- | --- | --- |
| 凭据决定 tenant | `core.py:QuotaKit.client` 查询 Client；Client 有 tenant_id | 兼容，业务路由只使用 caller.tenant_id |
| 只读/写入独立授权 | `core.py:CLIENT_ROLES` 只有 issuer/provider/consumer/membership；Client 单一 role | 缺少对应角色；不能复用 membership 让只读方拥有写会员能力 |
| 管理端认证 | `api.py:create_app`、`admin_auth.py:require_admin_factory` | 可复用管理员 Bearer/会话，不向 SKZY 发管理员凭据 |
| subject_id | `core.py:normalize_subject`、`models.py:SUBJECT_ID_TYPE` | 原样复用；1–128 字符，A–Z/a–z/0–9/`.`/`_`/`:`/`@`/`-`；纯数字须 >0；大小写与前导零有意义 |
| 有序多包 | `models.py:Assignment`、`level_packages.py` | Level 包有独立实例、顺序、周期计数，优先复用 |
| 旧辅助额度包 | `models.py:Package/PackageGrant`、`core.py:put_package_grant`、`docs/api.md` 的 Legacy top-ups | 是单 Service 补量，不独立赋予 Level 权限，不能无声等同于有序 Level 包 |
| 发放后权益稳定 | `_level_sources` 每次查实时 Limit；`put_limit` 允许改数量 | 仅保存 revision/审计 JSON 不足；新增初始化包须用额度快照参与实际计算 |
| 不覆盖已有权益 | `save_user_packages` 撤销未出现在完整列表的包 | 此方法不可用于默认发放；新增同事务 append 路径 |
| 并发与事务 | `sessions.begin`、`_lock_subject` upsert+行锁；SQLite BEGIN IMMEDIATE | 可复用锁和 Session；不能循环调用各自提交的公开发放方法 |
| 初始化记录 | models 中没有对应事实表 | 新增 tenant+subject 唯一事实，包撤销不得级联删除它 |
| 新会员目录与购买回执 | `core.py:membership_catalog`、`membership_purchases.py`、`models.py:MembershipPurchase` | 可参考 subject 锁和回执事务结构；目录仍依赖可写 membership 角色，购买以 tenant+order 为幂等边界且不同参数重试报冲突，不能替代默认初始化 |
| 错误外壳 | api 中 QuotaError → code/message；HTTPException/校验错误 → detail | 保留两种现有外壳，不能承诺当前所有错误统一 code/message |
| 目标 API | `api.py` 无两个目标路由 | 必须新增，不能对外宣称 v0.13.3 可调用 |

源文件在 `src/tekes_quota_kit/`。现有权限函数有一个实现注意事项：
`client(key, role)` 对 membership 以外会接受 consumer，因此新增角色必须显式隔离，
不能只添加角色常量就意外赋予所有 consumer 初始化权限。
新增会员购买会以 `package_code=membership` 发放主会员并遮蔽之前主会员；默认赠送必须是
独立追加包，不能借此切换或遮蔽付费会员。新 Level 的 `publication_status=test/formal`
是另一维度；建议默认方案只引用 formal Level，方案 enabled 不代替 Level 发布状态，
此项包配置校验也随 B 一并确认。

## 待确认的最小补充（未生效）

### A. 授权

建议沿用 Client 的凭据哈希、单 tenant、单 role 机制，新增两个独立角色：
`default_catalog`（仅 GET 目录）和 `default_init`（仅 POST 初始化），
均使用无 Service 绑定的占位方式。SKZY admin/backend 分别持不同凭据。
不扩建通用 scope/RBAC 体系，不让 consumer 或 membership 自动获得新能力。
需同步 provision、配置校验、管理 UI 和下载调用合同。此为现有角色机制的扩展，
不是声称现有版本已具备细分权限。两者均适配现有 role 的 String(16)，无需扩列。

### B. 包、快照、期限和顺序

建议 v1 仅支持有序 Level 包：方案条目 `code` 为方案内稳定包模板标识（不是用户实例 ID），
`name` 为包显示名；内部引用现有 `level_code`。不混入 legacy Service top-up。
一次发放内 `package_ids` 为按方案顺序排列的 Assignment.id 的十进制字符串，
消费方按不透明字符串保存；不同身份命名空间不能混用。

建议每个条目明确 `validity_days`：正整数表示从实际 applied_at 起 N×24 小时，
null 表示永久；`level_term` 必须有期限。立即生效，term_start=applied_at，
term_end=expires_at。保存计划时拒绝不满足规则的条目，不给消费方猜默认有效期。
条目数建议 1–100。已付费包优先级保持不动，赠送包按方案顺序追加到列表尾；
方案中的有序不意味着自动插到付费权益之前。

方案发布修订复制各条目的 Level 限额（quota_code、mode/value、period/timezone）；
发放记录保存实际修订和来源，Assignment 关联不可变额度快照。
默认包计算走现有周期、用量、分摊、退款引擎，仅额度来源改读冻结值；普通包继续实时 Level 语义。
仅改方案成员、名称、启停或原 Level 数值均不能追溯修改已发权益。
平台 Service 启停仍服从现有平台策略，不能通过赠送绕过它。
如消费方需要一次性单 Service 补量或不同有效期，应在合同中明确后再实施。

### C. 分页

建议保留示例所有字段，增加 `limit`（默认 100，1–100）、`offset`（默认 0，≥0），
响应 `{items, total, limit, offset}`；按不变的创建主键升序、tenant 内过滤、包含停用方案。
消费方累加 offset 至读完 total；不能只取第一页。方案不物理删除，修改不改变排序。
每次响应为一次读取时的结果；跨页不承诺事务快照，并发新增可下次刷新看到。
这项是 v1 原文要求但尚未定义的分页合同补充，不是已确认响应变更。

### D. 参数、错误和重放顺序

建议成功首次/重放均 HTTP 200，UTC 时间为 RFC3339 `Z` 格式。
方案/初始化 ID 由服务生成；本版本建议 token_urlsafe(24)，消费方只视为字符串，
不得解析格式。`default_plan_id` 非空、≤128 字符。source 四字段均必填非空字符串，
建议 system/event ≤64、estate_id/binding_id ≤128；拒绝未知字段（包括 tenant_id），
来源只审计，绝不当授权凭据。首次来源保留不覆盖。

| 情况 | 建议 HTTP / 外壳 |
| --- | --- |
| 不存在/其他 tenant 的方案 | 404 `default_plan_not_found`，code/message |
| 首次请求停用方案 | 409 `default_plan_disabled`，code/message |
| 无凭据或角色不符 | 沿用 401 `unauthorized`；缺 Bearer 沿用 401 detail |
| 字段类型/长度/缺失/非法 subject | 422 detail（FastAPI 校验） |
| 数据库或服务故障 | 新接口 503 `default_initialization_unavailable`，code/message；不暴露数据库细节 |

已有事实的优先级：认证与输入结构校验 → tenant+subject 查事实 → 若存在立即重放；
之后才查请求方案是否存在/启用。不要求 malformed 请求绕过校验，也不允许撤销的凭据重放。
合法结构下改传另一个不存在/停用方案也重放首次事实，不覆盖原来源和时间。
服务故障响应是否已提交可能不确定；调用方重试同一 subject，不能用换 binding_id 补领。

## 拟实施设计（合同补充确认后）

1. 管理端新增默认方案页及 tenant 范围 CRUD/启停接口；ID 创建后不可编辑，不物理删除。
   名称、内容、启停修改递增 revision，修订保存完整有序条目/额度快照；
   写入携带预期 revision，冲突显式返回，避免悄悄覆盖管理员修改。
2. 新增 default plan、不可变 revision/条目、default initialization 和包快照关联模型。
   初始化事实唯一约束 `(tenant_id, subject_id)`（该表只表示默认初始化操作）。
   subject 类型/排序规则复用 SUBJECT_ID_TYPE；事实保存原方案 ID、revision、UTC 时间、
   source 快照、有序实例 ID。重放读取事实，不依赖包是否仍有效或模板是否启用。
3. 初始化认证后开启一个 Session 事务；先 `_lock_subject`，锁内查初始化事实；
   已有则返回。首次锁定方案当前状态/修订，与方案编辑/停用保持一致的锁顺序，
   从一个完整修订构造所有新 Assignment/快照、递增 SubjectPackages.revision、写事实，
   最后一次提交。所有写入共享同一 db，不借用自行开启事务的公开保存函数。
4. 初始化与用户包编辑共享 subject 锁，旧 revision 的全列表编辑应冲突，不能覆盖新赠送。
   数据库唯一约束是最终防线。并发冲突应重新读已提交事实或明确报可重试错误，
   不吞故障，不返回虚构成功；事务提交完成后才返回 applied。
5. 撤销、到期、会员结束仍可改变包可用性，但不得删除初始化审计事实或快照。
   不增加 SKZY 归属查询、不修改 SKZY、不部署生产。

## 迁移与验证边界

实施交付须同时提供增量 MySQL DDL、新安装 create_tables_mysql.sql、模型、
SQLite 升级办法和 API 文档。create_all 只适合创建缺表，不能当作已有表升级工具。
旧 Assignment 快照关联为空并保留原语义；默认新包必须有关联。
新表/索引按 tenant 和 subject 隔离，MySQL 必须验证大小写/前导零不折叠。
迁移先在可丢弃旧版库备份副本演练；保留失败和迁移结果。无生产执行。

| 验收 | SQLite（实施后必须） | MySQL（实施后必须） |
| --- | --- | --- |
| 多包、顺序、付费权益保持 | 文件库集成测试，消费和退款验证 | InnoDB 真实引擎同案例 |
| 任意中间失败全回滚 | 注入第 N 包/事实写失败，核对无残留 | 同案例，验证事务行为 |
| 重复/并发/不同方案竞争 | 多连接；恰一初始化，所有响应同事实 | 多连接真实行锁/唯一约束，不能由 SQLite 代证 |
| 响应丢失、重启重试 | 提交后丢弃响应，新建 Kit 重试 | 新建连接/进程后重试 |
| 编辑/停用/不存在方案重试 | 相同事实；新用户按新状态处理 | 并发编辑/停用锁定读的一致性 |
| 快照生效 | 改方案及原 Level 后，旧包消费额度不变 | 同案例 |
| tenant/subject 隔离 | 相同 subject、不同 tenant；大小写与前导零 | 验证实际字符集与 collation |
| 到期/撤销/换小区 | 初始化仍重放，不能重新领取 | 同案例 |
| 授权/分页/错误 | HTTP 测试，跨页包含停用，角色越权拒绝 | HTTP 语义可复用；数据库部分另测 |
| 升级 | 旧 SQLite 升级及重新打开 | 旧 schema 增量升级、新安装 DDL 对齐 |

现有 MySQL 测试只在 `TEKES_QUOTA_TEST_DATABASE_URL` 指向可丢弃的
`tekes_quota_test_*` 数据库时运行。跳过不是通过，现有测试也不证明新合同成立。

## 转交 SKZY 与本批交付记录

- SKZY 可按“保存的共同接口合同 v1”对齐职责和成功载荷；四项补充确认前不要锁定角色、
  分页或包期限细节。当前 main 包元数据 v0.16.2 无目标 API，不能进行真实发放联调。
- 实施完成后提供确切发布版本/commit、迁移说明、两种独立凭据的 provision 方法，
  在非生产建立方案，GET 验证所有分页和停用展示，POST 首次/跨方案重试核对事实完全一致，
  检查付费包与实际消费，并回填 Issue/PR。不向文档写真实密钥。
- 本批仅文档；运行时功能、迁移、管理 UI、新 API 及其验收均未交付。
- 合同与交付跟踪：[Issue #40](https://github.com/TekesApps/TekesQuotaKit/issues/40)。
  合同冻结后的实施：[Issue #41](https://github.com/TekesApps/TekesQuotaKit/issues/41)，依赖 #40。
- 初始基线检查：`.venv/bin/python -m pytest -q tests/test_subject_id.py
  tests/test_membership_sync.py tests/test_ordered_level_packages.py tests/test_packages.py
  tests/test_admin_auth.py tests/test_docs_schema.py tests/test_mysql_integration.py`：
  **81 passed, 5 skipped**；五项 MySQL 测试因未配置可丢弃测试库而跳过。
  这些仅证明相关既有行为的基线，不是默认初始化功能验收。
  一条现有 Starlette/httpx 弃用警告，不影响本批结果。
- 更新至 `d03b7b7` 后，重跑上述集合并增加 `test_membership_catalog.py`、
  `test_membership_purchases.py`、`test_level_publication.py`、`test_service_status.py`：
  96 passed、5 skipped、1 failed。失败为本地 editable 安装元数据仍是 0.13.3，
  `test_session_reports_the_installed_version` 与新 pyproject 的 0.16.2 不一致。
  执行 `uv pip install --python .venv/bin/python --no-deps -e .` 同步元数据，
  单独重跑该失败测试：1 passed；其余通过项不因仅元数据刷新重复执行。
  合并检查结果为 97 项通过、5 项 MySQL 跳过；不是一次全绿运行，新功能仍未验证。
- 本批文档提交及 PR 链接回填 #40/#41；无新功能版本、无可调用的新 API。
