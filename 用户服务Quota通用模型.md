# 用户服务 Quota 通用模型

状态：设计讨论稿（2026-09-25）。本文定义通用结构与语义，不批准任何具体会员档位、价格、服务限额或生产数据迁移。

## 1. 目标

用户的 Profile 归属一个可定义的 `level`。每个 Level 包含若干 Quota，每个 Quota 包含一个或多个原子 Service。每个 Quota 定义自己的计量单位；独立的 Limit 定义某 Level 对某 Quota 在一个周期内可使用的总量。系统根据用户实际使用记录判断这些 Service 当前是否可用、已用多少、还剩多少。

模型保持以下关系：

```text
用户 Profile → Level → 若干 Quota → 若干原子 Service
Limit → 某个 Level 对某个 Quota 的周期与用量上限
业务使用 → Quota 消费记录
```

Service 是业务可识别的原子能力；Quota 是包含一个或多个 Service 的额度组，`quota_code` 是这个额度组的稳定标识和用量归集键。一个 Level 获得某个 Quota，就能使用该 Quota 包含的所有 Service；不会在同一个 Quota 内再按 Level 排除部分 Service。若需要只开放其中一个 Service，就为它定义单独的 Quota。Quota 核心只处理额度组、周期、单位、用量和消费，不内置“测量会话”“测量指标”等业务类型。

## 2. 核心概念

| 概念 | 含义 | 稳定标识 |
| --- | --- | --- |
| Profile Level | 用户当前拥有的一组 Quota | `level_code` |
| Service | 可独立识别、配置和产生使用事件的业务能力 | `service_code` |
| Quota | 包含若干 Service、具有统一计量单位和独立用量的额度组 | `quota_code` |
| Quota Service 成员 | 某 Quota 包含哪些原子 Service | `(quota_code, service_code)` |
| Limit | 某 Level 对某 Quota 的用量上限与重置周期；也表示该 Level 拥有此 Quota | `(level_code, quota_code)` |
| 使用事件 | 一个主体的一次应计费使用对一个 Quota 的实际消耗或退回 | `(subject_id, usage_key, quota_code)` |

`service_code` 与 `quota_code` 不要求相同。一个 Quota 可以包含多个 Service；同一 Service 在同一时点只属于一个用于计费的 Quota，以免消费归属不明确。同一 Quota 内的 Service 使用同一种计量单位。Level 没有对应的 Limit，就不拥有该 Quota，也不能使用其中的 Service。无需限制用量时配置显式的“不限量”Limit；它仍记录实际用量。有限模式下 `limit_value = 0` 表示不可用，不用 `0` 表示不限量。按大模型 token 计量的 Quota 可以设 100 万 token 的 Limit，调用次数本身无需另设“不限次”Limit。

## 3. 最小数据结构

以下是逻辑结构，不是立即执行的迁移 SQL。每张物理表使用自增整数 `id` 主键；`*_code` 是跨版本稳定的业务标识。首个实现将表名加 `tq_` 前缀，放入接入方现有 MySQL 数据库；不要求 TekesQuotaKit 独占数据库。`subject_id` 是接入方提供的正整数主体 ID（MySQL `BIGINT`，SQLite `INTEGER`），数康直接使用住户 ID；它与 `tenant_id` 一起确定主体，不在通用表中依赖数康用户表。

| 结构 | 必要字段 | 约束与职责 |
| --- | --- | --- |
| `profile_levels` | `id`, `level_code`, `name`, `status` | `level_code` 唯一；负责等级定义 |
| 用户 Profile 的 Level 归属 | `subject_id`, `level_code`, `effective_at`, `expires_at` | 记录当前等级及有效期；变更保留历史 |
| `services` | `id`, `service_code`, `name`, `status` | `service_code` 唯一；登记原子 Service |
| `quota_definitions` | `id`, `quota_code`, `name`, `unit_code`, `metering_mode`, `status` | `quota_code` 唯一；`unit_code` 如 `use`、`model_token`，`metering_mode` 为按次或按实际用量；不编码具体业务类型 |
| `quota_services` | `quota_code`, `service_code`, `effective_at` | 定义 Quota 的原子 Service 成员；一个 Service 在同一时点最多属于一个计费 Quota |
| `quota_limits` | `level_code`, `quota_code`, `limit_mode`, `limit_value`, `period_kind`, `timezone`, `effective_at` | 独立的 Limit 配置；一个生效版本对应一个 Level 和一个 Quota，`limit_mode` 为有限或不限量；数值使用该 Quota 的单位 |
| `user_quota_usage` | `subject_id`, `quota_code`, `period_start`, `period_end`, `limit_snapshot`, `used_units` | 当前周期已用量；主体、Quota、周期唯一；按次在兑现时、按实际用量在结算时原子更新 |
| `quota_tokens` | `token_id`, `subject_id`, `service_code`, `quota_code`, `usage_key`, `assignment_version`, `status` | Guard 内部保存准入与结算记录；按次直接兑现时创建记录并扣减，按实际用量首次申请时创建待结算记录 |
| `quota_usage_ledger` | `subject_id`, `service_code`, `quota_code`, `usage_key`, `event_key`, `delta_units`, `reverses_ledger_id`, `period_start`, `assignment_version`, `limit_version`, `created_at` | 不可变消费/退回流水；消费的 `(subject_id, usage_key, quota_code)` 唯一；退回以唯一事件键去重并指向原消费 |

上述结构中的用户 Level 归属可以落在用户主档并配一张变更历史表；具体物理拆表留待实施设计确定。`quota_usage_ledger` 是审计依据，`user_quota_usage` 与 `quota_tokens` 是并发控制和执行授权的状态。按次使用的准入、扣减和流水必须在同一事务中完成；按实际用量使用的准入状态在首次申请时写入，实际扣减、流水和结算状态在结算事务中完成。Quota 的 Service 成员关系与计量单位、Limit 变更不能覆盖历史版本。

按次 Quota 每次有效使用消耗 1 个 `use` 单位；按实际用量 Quota 消耗结算时报告的 `consumed_units`，例如大模型调用实际使用的 token 数。Limit 的周期先支持 `day`、`month`、`level_term`；日与月采用该 Limit 配置的业务时区，周期为左闭右开区间。`level_term` 随主体当前 Level 的有效期结束。同一个 Quota 在不同 Level 中可以有不同 Limit；实际用量只按主体、`quota_code` 和周期统计。跨周期执行的使用归属首次准入时确定的周期，结算仍写回该周期。

## 4. 查询与强制控制契约

Guard 对纳入 Level/Quota 管理的用户业务提供查询和两种准入方式。**查询接口**只用于展示，不能代替准入。**按次 Service** 在提供服务前调用 `redeem(subject_id, service_id[, request_key])`；成功表示本次已获准，且 1 个 `use` 单位已经原子扣除。**按实际用量 Service** 在提供服务前调用 `issue(subject_id, service_id[, request_key])` 获得准入及 token，服务结束后调用 `settle(token, consumed_units)`。这里的凭证 token 与作为计量单位的大模型 token 是不同概念。调用这些接口的必须是可信后端，用户身份须先由业务后端认证；前端不能自行提供可信的 `subject_id` 或持有服务端凭据。

查询某个用户的 Quota 状态时：

1. 从已认证的用户身份取得当前有效 Level。
2. 找到包含该 Service 的 Quota，并检查该 Level 是否有此 Quota 的 Limit。
3. 检查 Level 和 Limit 的有效期；有限额度再读取当前周期的用量。
4. 返回 `available`、`unit`、`limit`、`used`、`remaining`、`periodEnd` 和不可用原因。没有 Limit、周期结束、额度耗尽应有不同原因。

准入与结算流程如下：

1. 业务后端用自身凭据、已认证用户的整数 `subject_id`、整数 `service_id` 调用对应模式的接口。Guard 根据 Service ID 找到 Quota，核对凭据所属租户与 Service、用户当前 Level 和 Limit。调用方无需保存 `service_code`、`quota_code` 或 `usage_key`。
2. 按次业务只调用一次 `redeem`。Guard 在同一数据库事务中检查剩余额度、登记这次使用、扣 1 并写流水；只有成功响应才能执行该次服务。多个请求争最后一个单位时只有一个成功。成功响应附带内部 token，供按次失败退回或状态查询使用；正常执行服务不需要再提交 token。`request_key` 可由业务请求 ID 提供，重复使用相同键不会再次扣减，也不会重新授权执行业务。未提供时，每次调用都视为独立使用，网络重试可能重复扣费。
3. 按实际用量业务第一次调用 `issue` 时完成准入，返回 token 并记录待结算状态，但不预扣用量。相同 `request_key` 的重试返回同一 token，标记为 `idempotent`，不表示可以重新执行服务。服务结束后，掌握实际用量的可信后端调用 `settle(token, consumed_units)`；Guard 用 token 查主体、Service、Quota 和首次准入的周期，在一个事务中记账并标记已结算。`consumed_units` 不得为负；零消耗也须结算为 0。同一数值的结算可安全重试，不同数值的重复结算报冲突。待结算记录保持有效，直到可信后端结算并完成对账。
4. 按实际用量模式不预留额度，因此并发请求的实际结算总量可能超过有限 Limit。业务接入时要控制并发和单次最大消耗。待结算记录必须能查询、补报或对账，不能仅凭超时假定消耗为 0。
5. 同一 Quota 的多个原子 Service 共享余额；一次按次调用对应一次实际使用。不同 Quota 分别记账。按次业务若已扣减却未能提供服务，可用成功响应中的 token 调用幂等退回；按实际用量业务即使中途失败，仍按实际消耗结算。

`usage_key` 是 Guard 内部的一次使用标识，不等同于 HTTP 请求 ID。业务方可以提供稳定的 `request_key` 来防止重试重复扣减或重复签发，但还须让业务操作本身幂等：数据库扣减与外部业务效果无法共用事务。遇到按次调用响应丢失时，不应换一个新键盲目重试；相同键返回已兑现错误及原 token，供查询原业务结果、检查状态或按规则退回，不构成第二次准入。返回的 token 用于退回或状态查询，不能转给另一个用户或 Service 使用。Quota 成员关系切换后，历史消费和结算仍须按首次准入时的 Quota 与周期解释。

## 5. 功能模块接入要求与使用方法

| 接入方 | 要求 |
| --- | --- |
| 发起端（小程序、企业微信等） | 调用业务后端并完成正常用户认证；可以展示 Quota 查询结果，但不能据此自行放行服务，也不能持有 QuotaKit 的服务端凭据。 |
| 按次业务后端 | 在产生受控业务效果前调用一次 `POST /v1/redeem`，请求体提供整数 `subject_id` 和 `service_id`，建议加稳定的 `request_key`。成功后执行一次业务；失败即停止。若已扣费但执行失败且符合退回规则，用返回的 token 调用退回接口。 |
| 按实际用量业务后端 | 在执行前调用 `POST /v1/token`，用整数 `subject_id` 和 `service_id` 取得 token 与准入；执行完成后调用 `POST /v1/token/settle`，提交 token 和实际 `consumed_units`。包括部分失败产生的用量。 |
| 后台任务、设备回调和其他入口 | 在真正开始提供服务的后端执行对应准入调用；不能因为是内部接口而跳过。异步任务应保存稳定业务请求 ID，按实际用量任务还应保存结算 token。 |

按次调用顺序：`redeem(subject_id, service_id) → 成功后执行服务`。按实际用量调用顺序：`issue(subject_id, service_id) → 成功后执行服务 → settle(token, consumed_units)`。所有调用都由已认证的后端完成；结算时 Guard 从 token 取得用户和 Quota，退回和状态查询使用的 `X-Subject-ID` 由后端根据已认证身份提供，而不是让终端自行指定。

小屋测量按现行业务规则在麦邦成功登录测量会话时计一次，而不是扫码开门时计次。因此按次 `redeem` 应发生在身份认证成功、准备开始受控测量服务的后端入口；扫码开门不预先扣次数。外部测量效果与扣减不能共用事务，失败补偿须按实际业务结果处理。

## 6. 测量服务例子：验证结构能否演进

假设测量有多个原子 Service，分别代表可独立识别的测量能力。下面只展示 Quota 的 Service 成员，不规定实际指标清单或会员数字。

| 规则阶段 | Quota 包含的 Service | 同一业务操作得到 10 项有效结果 | 剩余 30 项 |
| --- | --- | --- | --- |
| 当前按一次测量计数 | 所有测量 Service 都属于 `measurement`；Level 配置此 Quota 的限额 | 相同 `usage_key`、`quota_code` 去重，消耗 `measurement` 1 单位 | 无消费 |
| 将来按各项独立计数 | 各测量 Service 分别属于独立的 Quota；Level 配置这些 Quota 的限额 | 对实际得到结果的 10 个 Quota 各消耗 1 单位 | 无消费 |

两个阶段使用相同的数据结构和消费接口。改变的是各 Quota 的 Service 成员，以及 Level 对新增 Quota 的 Limit。逐项计数必然需要不同的 `quota_code`，因为每项要有独立的已用量和剩余量；可以批量生成相同的 Limit，不需要给 Quota 核心添加“指标”字段。

Quota 成员关系切换应从明确的新周期或新权益生效点开始。旧的“已用 3 次测量”不能自动解释为“每项已用 3 次”；历史账本保留原来的 Quota code 和 Limit 版本。任何历史额度转换都需要单独制定迁移规则。

**当前实现差异**：现有健康小屋在测量会话身份认证时更新 `resident_entitlements.measure_used`，并写入 `measurement_usage_ledger`，早于逐项有效测量结果的确认。如果产品要求“至少有一项有效结果才算使用”，接入通用 Quota 时还需要调整业务扣减时点。旧测量权益与新 Quota 不得同时扣减。[数康现有扣减入口](https://github.com/qf0421/shukang-zhiyi/blob/main/services/wecom-qr/wecom_qr/health_house_service.py)

当前 Profile 接口返回的 `level` 仍是固定展示值，不能直接当作授权来源。接入前需要建立用户当前 Level 的持久化归属，并由后端据此判定可用 Service。[数康现有 Profile 代码](https://github.com/qf0421/shukang-zhiyi/blob/main/services/wecom-qr/wecom_qr/ai_health_steward_service.py)

## 7. 首版边界与待定事项

- 先实现按用户的 Service 使用额度；会员价格、家庭共享、优惠券、线下预约等不纳入 Quota 核心。
- 样例会员方案只用于验证模型可表达未来档位，不作为上线配置。Level 名称、Service 清单、Quota code、限额数值和周期由后续产品配置确定。
- Level、限额或绑定发生变更时，现有周期保持创建时的 `limit_snapshot` 与映射历史；默认下一周期生效。中途升级、降级、退款和补发额度需另行定义业务规则。
- Service 的原子边界和按次业务的有效使用时点由业务模块定义；实际用量单位及可信的用量生产方由接入配置定义。业务模块调用对应准入接口，不依赖 Quota 内部的 `service_code` 或 `usage_key`。
- 首版验收至少覆盖：查询结果不能代替准入、按次单次调用原子扣减且同键重试不重复扣减、按次并发争最后一个单位时仅一个成功、按实际用量首次调用即准入且不预扣、结算可能有限超额、重复结算幂等、未结算凭证可对账、无 Limit、额度耗尽、凭证不能跨用户或 Service 使用、按次失败的幂等退回、多个 Service 共享余额、不同 Quota 分别使用、周期重置、Limit 变更后历史流水可解释。
