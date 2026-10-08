export type Row = Record<string, unknown>;
export type User = { username: string | null; expires_at?: string; via: 'session' | 'admin_key' };

export const text = (value: unknown) => value == null || value === '' ? '—' : String(value);

/** API timestamps are naive UTC ("2026-10-08T06:00:00Z"); show them in the viewer's zone. */
export const time = (value: unknown) => {
  if (!value) return '—';
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
};

export const statusNames: Record<string, string> = {
  active: '启用', disabled: '停用',
  admitted: '已准入', settled: '已结算', refunded: '已退款',
  open: '进行中', closed: '已结束', expired: '已过期', available: '可用', used: '已使用',
  consume: '扣减', refund: '退还',
  finite: '有限', unlimited: '不限',
  per_use: '按次', reported_usage: '按用量',
  atomic: '原子', composite: '组合', instant: '即时', durable: '持续',
  day: '每日', week: '每周', month: '每月', level_term: '会员期',
  issuer: '准入方', provider: '执行方', consumer: '准入+执行',
};

/** Column titles shared by every table view. */
export const columnNames: Record<string, string> = {
  id: 'ID', tenant_id: '租户', client_id: 'Client ID', role: '角色',
  quota_code: 'Quota', unit_code: '单位', metering_mode: '计量方式',
  level_code: 'Level', limit_mode: '限额方式', limit_value: '限额', period_kind: '周期', timezone: '时区',
  service_code: 'Service', service_kind: '类型', redemption_mode: '兑现方式', charge_units: '开场扣费', session_ttl_seconds: '默认时长(秒)',
  parent_service_code: '组合 Service', child_service_code: '子 Service', max_uses: '每场上限',
  subject_id: '用户 ID', effective_at: '生效时间', expires_at: '到期时间', term_start: '会期开始', term_end: '会期结束',
  period_start: '周期开始', period_end: '周期结束', used_units: '已用',
  usage_key: '用量键', request_key: '请求键', issuer_client_id: '准入方', provider_client_id: '执行方',
  status: '状态', admitted_at: '准入时间', consumed_units: '消耗', session_status: '场次状态',
  session_expires_at: '场次到期', closed_at: '结束时间', token_id: '凭证 ID', slot_no: '序号', used_at: '使用时间',
  event_type: '事件', delta_units: '变动', created_at: '时间',
};
