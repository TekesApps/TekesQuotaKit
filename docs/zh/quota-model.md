# 用户服务 Quota 通用模型

> 说明：本文为设计笔记。公开的权威参考以英文 [README](../../README.md) 与 [docs/api.md](../api.md) 为准。

本文说明 TekesQuotaKit 当前的数据关系。旧版关于“Quota 直接包含若干原子 Service，并用相同 `usage_key` 合并测量扣费”的讨论已由 Git 历史保留，不是当前实现。完整的测量配置与调用过程见 [组合 Service 与测量示例](composite-measurement-example.md)。

## 三个独立概念

- **Service** 是可使用的功能。`tq_services` 中每个 `(tenant_id, service_code)` 只定义一次，例如 `blood_pressure`。
- **Quota（配额）** 是计数的种类，只定义计什么，不含数量。`tq_quotas` 中每个 `(tenant_id, quota_code)` 定义单位和计量模式，例如 `measurement_count`。
- **Level** 是会员等级。`tq_limits` 以 `(tenant_id, level_code, quota_code)` 指定该等级在每个周期对某种配额的额度（数量）；`tq_assignments` 指定用户当前属于哪个等级。

Service 通过自身的 `quota_code` 选择直接使用时扣哪种 Quota。组合 Service 也是 `tq_services` 的一条记录，它有自己的 `quota_code`；子 Service 由 `tq_service_members` 关联到组合 Service。Quota 本身不存子 Service 清单，也不负责猜测多次调用是否属于同一场。

## 11 张表的职责

| 表 | 主要连接字段 | 职责 |
| --- | --- | --- |
| `tq_levels` | `tenant_id`, `level_code` | 定义会员等级 |
| `tq_quotas` | `tenant_id`, `quota_code` | 定义配额种类、单位、计量模式 |
| `tq_limits` | `level_code`, `quota_code` | 定义等级额度：等级在每个周期对配额能用多少 |
| `tq_assignments` | `subject_id`, `level_code` | 保存用户等级及生效、到期和会期时间 |
| `tq_services` | `service_code`, `quota_code` | 定义原子或组合 Service，以及 `instant` 或 `durable` 使用模式 |
| `tq_service_members` | `parent_service_code`, `child_service_code` | **组合映射表**；每行表示某组合包含某原子 Service |
| `tq_clients` | `service_code` | 将可信调用方绑定到对外调用的 Service |
| `tq_usage` | `subject_id`, `quota_code`, `period_start` | 保存用户当前周期对各配额的已用数量 |
| `tq_tokens` | `subject_id`, `service_code`, `quota_code` | 保存一次准入、扣费状态及可选的持续使用状态 |
| `tq_token_items` | `token_id`, `child_service_code` | 保存持续凭证的子项资格快照与每次 `use` 记录 |
| `tq_ledger` | `token_hash`, `quota_code`, `delta_units` | 保存消费和退款流水 |

每张表都有整数 `id` 主键。跨配置表使用稳定的 `*_code`；特别是 `tq_service_members` 中父、子两个 code 都指向 `tq_services`，并以 `(tenant_id, parent_service_code, child_service_code)` 保证同一组合内不重复。

## 一个子 Service 属于多个组合

假设两个组合使用不同 Quota，但都允许测血压：

| 表 | 示例记录 |
| --- | --- |
| `tq_services` | `blood_pressure`，原子 Service，**只有这一条定义** |
| `tq_services` | `measurement_session`，组合 Service，`quota_code=measurement_count` |
| `tq_services` | `followup_session`，组合 Service，`quota_code=followup_count` |
| `tq_service_members` | `measurement_session → blood_pressure`，`max_uses=NULL` |
| `tq_service_members` | `followup_session → blood_pressure`，`max_uses=2` |

两条 member 记录只是两段归属关系，并没有复制血压 Service。`max_uses` 属于**这一段组合关系**：`NULL` 表示一场内不限次数，正整数表示上限。`redeem` 把当时的成员关系和次数上限复制到 `tq_token_items`；之后修改配置不改变已开始的场次。

用户通过 `measurement_session` 开场就扣 `measurement_count`，通过 `followup_session` 开场就扣 `followup_count`。场内 `use(token, blood_pressure)` 只验证该凭证包含血压、场次仍有效及该关系的次数上限，不再扣 Quota。同一原子 Service 也可以配置自己的 `quota_code`，供它被直接 `redeem` 时单独计费；组合使用仍以父 Service 的 Quota 扣费。

多个 Level 可以在 `tq_limits` 分别配置同一 Quota 的不同额度；Level 与子 Service 没有直接映射表。因此不要把 `tq_service_members` 当成 unit Service 表，也不要把一条 member 关系当成一次消费。

## 调用与边界

- `instant`：`redeem` 一次完成准入和扣费。
- `durable`：`redeem` 扣一次并打开凭证；期间每次子项操作调用 `use`；`stop` 结束，可选时长到期后 `use` 失效。
- `reported_usage`：`issue` 准入，`settle(token, consumed_units)` 按实际用量结算。

这些表是 Kit 的授权和计次状态，不表示物理测量设备的归属。若 `measurement_station` 指某台设备或站点实例，其业务 ID 仍需由消费者管理，并作为稳定业务请求 ID 参与调用。生产接入还需核对真实会员权益、测量项目与旧扣费入口，避免新旧系统同时扣减。
