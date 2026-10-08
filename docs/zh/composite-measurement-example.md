# 组合 Service 与测量示例

> 说明：本文为设计笔记。公开的权威参考以英文 [README](../../README.md) 与 [docs/api.md](../api.md) 为准。

本文描述当前版本（0.2.x）的实现。以下会员数值与测量项目代码是配置示例，生产值需要和业务记录核对。Kit 统一管理 Level、Quota、Limit、Service、组合成员和计次规则；可信业务后端提供已认证用户、目标 Service 与稳定的请求 ID。`instant` 凭证在 `redeem` 时完成；`durable` 凭证由 `redeem` 开启，期间每次 `use` 校验项目，可选超时，最终由 `stop` 结束。

## 从零配置

Kit 共 11 张表：原有 `tq_clients`、`tq_levels`、`tq_quotas`、`tq_services`、`tq_limits`、`tq_assignments`、`tq_usage`、`tq_tokens`、`tq_ledger`，新增 `tq_service_members` 和 `tq_token_items`。所有表都有整数主键 `id`。在租户 `demo-tenant` 下，以用户 42 的基础会员每个会期可开始两场测量为例：

1. `tq_quotas` 增加 `quota_code=measurement_count`、`unit_code=use`、`metering_mode=per_use`。
2. `tq_levels` 增加 `level_code=basic`。
3. `tq_limits` 增加 `level_code=basic`、`quota_code=measurement_count`、`limit_mode=finite`、`limit_value=2`、`period_kind=level_term`、`timezone=Asia/Shanghai`。
4. `tq_services` 增加以下七条。组合父项关联 Quota；子项暂不直接关联 Quota。`service_code` 是项目的稳定身份。

| `service_code` | `service_kind` | `redemption_mode` | `quota_code` | `charge_units` | `session_ttl_seconds` |
| --- | --- | --- | --- | ---: | ---: |
| `measurement_session` | `composite` | `durable` | `measurement_count` | 1 | NULL |
| `blood_pressure` | `atomic` | `instant` | NULL | NULL | NULL |
| `blood_oxygen` | `atomic` | `instant` | NULL | NULL | NULL |
| `body_temperature` | `atomic` | `instant` | NULL | NULL | NULL |
| `body_composition` | `atomic` | `instant` | NULL | NULL | NULL |
| `body_circumference` | `atomic` | `instant` | NULL | NULL | NULL |
| `ecg` | `atomic` | `instant` | NULL | NULL | NULL |

5. `tq_service_members` 增加六条，`parent_service_code` 均为 `measurement_session`，`child_service_code` 分别是上表六个原子 Service，`max_uses` 全部为 `NULL`。这一列为空表示场内不限次数；需要限制时填正整数，例如 2。主键 `id` 只标识关系记录，`(tenant_id, parent_service_code, child_service_code)` 唯一且两个 Service code 都外键指向 `tq_services`。同一个子 Service 可以出现在多个组合 Service 下，各关系可设置不同的 `max_uses`。Level 不直接关联子 Service；通过 `tq_limits` 的 `(level_code, quota_code)` 授予父 Service 所用的 Quota，所以多个 Level 可以分别给同一父 Service 配不同额度。

   这六条是映射记录，**不是六个新的 Service 定义**。若另有 `followup_session`（父 Service 使用 `followup_count` Quota）也包含血压，只在 `tq_services` 新增 `followup_session`，并在 `tq_service_members` 新增 `followup_session → blood_pressure`；现有 `blood_pressure` 定义保持一条。两个父 Service 可关联不同 Quota，血压在两个组合内的 `max_uses` 也可不同。
6. `tq_assignments` 为用户 42 增加 `level_code=basic`，`effective_at=2026-10-01 00:00:00 +08:00`，`expires_at=2026-11-01 00:00:00 +08:00`；`term_start`、`term_end` 保存相同会期边界的 UTC 时刻。管理员 API 接受带时区时间。中途换级沿用原会期和已用量；明确 `renew_term=true` 才开始新会期。
7. `tq_clients` 为可信测量后端增加 `consumer` 凭据，绑定 `service_code=measurement_session`。凭据不交给小程序或设备。

配置完成而用户尚未测量时，`tq_usage`、`tq_tokens`、`tq_token_items`、`tq_ledger` 均没有该用户的测量消费记录。

## 一场测量的表变化

1. 身份确认后，业务后端调用 `redeem(subject_id=42, service_code=measurement_session, request_key=业务会话ID)`，可选传 `duration_seconds=3600`。Kit 检查 Level、Limit，在一个事务里创建或更新 `tq_usage`，令 `used_units` 从 0 变 1；`tq_tokens` 新增 `status=settled`、`session_status=open`、`consumed_units=1` 的凭证；`tq_ledger` 增加 `delta_units=+1`；`tq_token_items` 新增六条 `available` 的项目资格快照，每项一条，`slot_no=0`，`max_uses=NULL`。不传时长且 Service 未配置默认时长时，凭证一直开放至 `stop`。
2. 每次准备执行项目时，后端调用 `use(token, child_service_code, request_key=本次子项请求ID)`。Kit 验证项目属于本场、凭证仍开放且未超时，随后在 `tq_token_items` 新增一条 `used` 尝试记录，写入 `request_key`、`used_at` 和递增的 `slot_no`。本例 `max_uses=NULL`，同一项目可反复调用；若关系上配置了正整数，则以开场时写入资格快照的数值为上限。每次 `use` 都不改余额和账本。相同请求 ID 重试返回原尝试记录，不会再新增一条。
3. 后端调用 `stop(token)` 后，`tq_tokens.session_status=closed` 并写 `closed_at`；六条资格快照改为 `expired`，已记录的尝试保留。扣费状态 `tq_tokens.status` 保持 `settled`，本场仍消费 1 次；缺失项目以后不能拿本凭证补测。
4. 若配置了时长而未调用 `stop`，超过 `session_expires_at` 的 `use` 立即拒绝。定时运行 `tekes-quota-kit expire-sessions`，持久化过期状态。没有时长的凭证不会自动过期，必须由消费者调用 `stop`。
5. 若一个子项都未使用，且业务确认应退费，可信后端显式调用 `refund(token)`：`tq_usage.used_units` 减 1，`tq_tokens.status=refunded`，`tq_ledger` 追加 `delta_units=-1`，场次关闭。有任一子项已使用时，Kit 拒绝整场退款。

`tq_token_items` 约束 `(token_id, child_service_code, slot_no)` 唯一、`(token_id, request_key)` 唯一。`slot_no=0` 表示项目资格快照，正数表示该项目的第几次尝试。`redeem` 相同请求 ID 返回同一凭证并标记 `idempotent=true`；业务不能因重试响应再次驱动设备。`tq_ledger` 仅存 `period_start`，完整会期边界保存在 `tq_usage` 和 `tq_tokens`。

## 其他模式与接入边界

普通 `instant` 按次 Service 用 `redeem(subject_id, service_code 或 service_id, request_key)` 一次准入、扣费并完成；按实际用量 Service 继续用 `issue` 准入、`settle` 报告真实用量。组合 `durable` Service 用 `redeem`/多次 `use`/`stop`，不把六项分别 `redeem`，也不依赖共享 `quota_code` 合并六次请求。

若以后改为每项独立配额，需要为六个子 Service 分别配置 `blood_pressure_count`、`blood_oxygen_count`、`body_temperature_count`、`body_composition_count`、`body_circumference_count`、`ecg_count` 六个按次 Quota，并为同一 Level 分别配置六条 Limit。将每个原子 Service 的 `quota_code` 改为对应 Quota。当前接口要求每项另配一条绑定该 Service 的 `tq_clients` 凭据；业务端使用对应凭据和稳定 `service_code` 分别调用 `POST /v1/redeem`。每次只扣该项目的额度，某项目耗尽不影响其他项目。

从整场模式切换时，先停止新整场准入：删除父 Service 的六条成员配置，并把父 Service 改为不带 Quota 的原子 Service。已经 `redeem` 的旧持续凭证仍依照 `tq_token_items` 快照核销，直到 `stop` 或超时；新的项目调用则按各自 Quota 扣费。切换窗口内可能同时存在两套有效凭证，需要业务后端按会话创建时间选择调用路径并完成对账。每个 Service 仍需要独立凭据，因此“业务只用同一凭据处理所有项目”的最简接口尚未实现。

生产接入需核对真实项目、会员权益、旧测量扣费入口、设备成功事件及历史额度转换。切换时必须停用旧系统的扣费计数器，避免双重计费。新建库 DDL 与旧库升级 SQL 分别见 [`migrations/create_tables_mysql.sql`](../../migrations/create_tables_mysql.sql)、[`migrations/add_composite_services_mysql.sql`](../../migrations/add_composite_services_mysql.sql)；`init-schema` 不修改旧表。
