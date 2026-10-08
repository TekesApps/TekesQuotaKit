import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { api, download, enc } from './api';
import { Actions, Editor, Pager, Status, Table, useCodes, useTable, type Column, type Field, type Option } from './components';
import { columnNames, type Row, text, time } from './types';

export type PageProps = { tenant: string; revision: number; onChanged: (message: string) => void };

const option = (value: string, label: string): Option => ({ value, label: `${label}（${value}）` });
const STATUS_KEYS = new Set(['status', 'session_status', 'metering_mode', 'limit_mode', 'period_kind', 'service_kind', 'redemption_mode', 'role', 'event_type']);
const TIME_KEYS = new Set(['effective_at', 'expires_at', 'term_start', 'term_end', 'period_start', 'period_end', 'admitted_at', 'session_expires_at', 'closed_at', 'used_at', 'created_at']);

function column(key: string, extra: Partial<Column> = {}): Column {
  return {
    key, title: columnNames[key] || key,
    render: STATUS_KEYS.has(key) ? row => <Status value={row[key]} />
      : TIME_KEYS.has(key) ? row => time(row[key])
      : key.endsWith('_code') || key === 'client_id' ? row => row[key] == null ? '—' : <span className="code">{text(row[key])}</span>
      : undefined,
    ...extra,
  };
}

type Spec = {
  table: string; title: string; description: ReactNode; addLabel: string;
  columns: Column[]; fields: Field[]; defaults?: Row; editable?: boolean;
  save: (tenant: string, data: Row) => Promise<unknown>;
  remove?: { label: string; confirm: (row: Row) => string; run: (tenant: string, row: Row) => Promise<unknown> };
};

function ConfigPage({ spec, tenant, revision, onChanged }: PageProps & { spec: Spec }) {
  const [offset, setOffset] = useState(0), [editing, setEditing] = useState<Row | null>(null), [creating, setCreating] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const result = useTable(spec.table, tenant, revision, offset);
  async function remove(row: Row) {
    if (!spec.remove || !window.confirm(spec.remove.confirm(row))) return;
    setBusy(true); setError('');
    try { await spec.remove.run(tenant, row); onChanged('已删除'); } catch (e) { setError(e instanceof Error ? e.message : '删除失败'); } finally { setBusy(false); }
  }
  const actions: Column = { key: '_actions', title: '操作', render: row => <Actions busy={busy} items={[
    ...(spec.editable ? [{ label: '编辑', action: () => setEditing(row) }] : []),
    ...(spec.remove ? [{ label: spec.remove.label, danger: true, action: () => void remove(row) }] : []),
  ]} /> };
  const columns = spec.editable || spec.remove ? [...spec.columns, actions] : spec.columns;
  return <section className="panel">
    <div className="panel-head"><div><h2>{spec.title}</h2><p className="muted">{spec.description}</p></div><button onClick={() => setCreating(true)}>{spec.addLabel}</button></div>
    {error && <div className="error-box" role="alert">{error}</div>}
    <Table columns={columns} data={result.rows} loading={result.loading} error={result.error} />
    <Pager offset={offset} total={result.total} onChange={setOffset} />
    {(creating || editing) && <Editor
      title={editing ? `编辑${spec.title}` : spec.addLabel} fields={spec.fields} editing={Boolean(editing)}
      initial={editing ?? spec.defaults ?? {}}
      onSave={async data => { await spec.save(tenant, data); onChanged(editing ? '已保存修改' : '已新增'); }}
      onClose={() => { setEditing(null); setCreating(false); }} />}
  </section>;
}

const put = (path: string, body: unknown = {}) => api(path, 'PUT', body);
const required = (value: unknown, label: string) => { if (value == null || value === '') throw new Error(`请填写${label}`); return String(value); };

export function QuotasPage(props: PageProps) {
  return <ConfigPage {...props} spec={{
    table: 'tq_quotas', title: 'Quota 额度', addLabel: '新增 Quota',
    description: '扣费的额度种类。创建后单位和计量方式不能修改。',
    columns: ['quota_code', 'unit_code', 'metering_mode'].map(k => column(k)),
    defaults: { unit_code: 'use', metering_mode: 'per_use' },
    fields: [
      { name: 'quota_code', label: 'Quota code', required: true, key: true, placeholder: 'measurement_count' },
      { name: 'metering_mode', label: '计量方式', required: true, options: [option('per_use', '按次'), option('reported_usage', '按用量')] },
      { name: 'unit_code', label: '单位', required: true, hint: '按次计量必须为 use' },
    ],
    save: (t, d) => put(`/tenants/${enc(t)}/quotas/${enc(required(d.quota_code, 'Quota code'))}`, { unit_code: d.unit_code, metering_mode: d.metering_mode }),
  }} />;
}

export function LevelsPage(props: PageProps) {
  return <ConfigPage {...props} spec={{
    table: 'tq_levels', title: 'Level 等级', addLabel: '新增 Level',
    description: '会员等级。每个等级在“额度规则”里给各 Quota 配额度。',
    columns: [column('level_code')],
    fields: [{ name: 'level_code', label: 'Level code', required: true, key: true, placeholder: 'basic' }],
    save: (t, d) => put(`/tenants/${enc(t)}/levels/${enc(required(d.level_code, 'Level code'))}`),
  }} />;
}

export function LimitsPage(props: PageProps) {
  const levels = useCodes('tq_levels', 'level_code', props.tenant, props.revision);
  const quotas = useCodes('tq_quotas', 'quota_code', props.tenant, props.revision);
  return <ConfigPage {...props} spec={{
    table: 'tq_limits', title: '额度规则', addLabel: '新增额度规则', editable: true,
    description: '某个 Level 对某个 Quota 的额度和周期。创建后周期和时区不能修改。',
    columns: ['level_code', 'quota_code', 'limit_mode', 'limit_value', 'period_kind', 'timezone'].map(k => column(k)),
    defaults: { limit_mode: 'finite', period_kind: 'month', timezone: 'Asia/Shanghai' },
    fields: [
      { name: 'level_code', label: 'Level', required: true, key: true, options: levels },
      { name: 'quota_code', label: 'Quota', required: true, key: true, options: quotas },
      { name: 'limit_mode', label: '限额方式', required: true, options: [option('finite', '有限'), option('unlimited', '不限')] },
      { name: 'limit_value', label: '限额', type: 'number', min: 0, hint: '不限时留空' },
      { name: 'period_kind', label: '周期', required: true, options: [option('day', '每日'), option('week', '每周（周一 0 点重置）'), option('month', '每月'), option('level_term', '会员期')] },
      { name: 'timezone', label: '时区', required: true },
    ],
    save: (t, d) => put(`/tenants/${enc(t)}/levels/${enc(required(d.level_code, 'Level'))}/limits/${enc(required(d.quota_code, 'Quota'))}`,
      { limit_mode: d.limit_mode, limit_value: d.limit_mode === 'unlimited' ? null : d.limit_value, period_kind: d.period_kind, timezone: d.timezone }),
  }} />;
}

export function ServicesPage(props: PageProps) {
  const quotas = useCodes('tq_quotas', 'quota_code', props.tenant, props.revision);
  return <ConfigPage {...props} spec={{
    table: 'tq_services', title: 'Service 服务', addLabel: '新增 Service', editable: true,
    description: '可使用的功能。组合 Service 的子项在“组合成员”里配置。',
    columns: ['service_code', 'service_kind', 'redemption_mode', 'quota_code', 'charge_units', 'session_ttl_seconds'].map(k => column(k)).concat(column('id', { title: 'service_id' })),
    defaults: { service_kind: 'atomic', redemption_mode: 'instant' },
    fields: [
      { name: 'service_code', label: 'Service code', required: true, key: true, placeholder: 'measurement_session' },
      { name: 'quota_code', label: 'Quota', options: quotas, hint: '子项可留空' },
      { name: 'service_kind', label: '类型', required: true, options: [option('atomic', '原子'), option('composite', '组合')] },
      { name: 'redemption_mode', label: '兑现方式', required: true, options: [option('instant', '即时'), option('durable', '持续')] },
      { name: 'charge_units', label: '开场扣费', type: 'number', min: 1, hint: '持续模式必填，即时留空' },
      { name: 'session_ttl_seconds', label: '默认时长（秒）', type: 'number', min: 60, max: 86400, hint: '可留空' },
    ],
    save: (t, d) => put(`/tenants/${enc(t)}/services/${enc(required(d.service_code, 'Service code'))}`, {
      quota_code: d.quota_code || null, service_kind: d.service_kind, redemption_mode: d.redemption_mode || null,
      charge_units: d.charge_units, session_ttl_seconds: d.session_ttl_seconds,
    }),
  }} />;
}

export function MembersPage(props: PageProps) {
  const services = useCodes('tq_services', 'service_code', props.tenant, props.revision);
  const path = (t: string, row: Row) => `/tenants/${enc(t)}/services/${enc(required(row.parent_service_code, '组合 Service'))}/members/${enc(required(row.child_service_code, '子 Service'))}`;
  return <ConfigPage {...props} spec={{
    table: 'tq_service_members', title: '组合成员', addLabel: '新增组合成员', editable: true,
    description: '组合 Service 包含哪些子 Service。每场上限留空表示不限次数。',
    columns: ['parent_service_code', 'child_service_code', 'max_uses'].map(k => column(k, k === 'max_uses' ? { render: r => r.max_uses == null ? '不限' : text(r.max_uses) } : {})),
    fields: [
      { name: 'parent_service_code', label: '组合 Service', required: true, key: true, options: services },
      { name: 'child_service_code', label: '子 Service', required: true, key: true, options: services },
      { name: 'max_uses', label: '每场上限', type: 'number', min: 1, hint: '留空为不限' },
    ],
    save: (t, d) => put(path(t, d), { max_uses: d.max_uses }),
    remove: {
      label: '删除', confirm: r => `删除 ${text(r.parent_service_code)} → ${text(r.child_service_code)}？已开始的场次不受影响。`,
      run: (t, r) => api(path(t, r), 'DELETE'),
    },
  }} />;
}

export function AssignmentsPage(props: PageProps) {
  const levels = useCodes('tq_levels', 'level_code', props.tenant, props.revision);
  return <ConfigPage {...props} spec={{
    table: 'tq_assignments', title: '用户等级', addLabel: '设置用户等级', editable: true,
    description: '把用户放到某个 Level。会期内换级沿用原会期，勾选“开始新会期”才重置。',
    columns: ['subject_id', 'level_code', 'effective_at', 'expires_at', 'term_start', 'term_end'].map(k => column(k)),
    fields: [
      { name: 'subject_id', label: '用户 ID', type: 'number', required: true, key: true, min: 1 },
      { name: 'level_code', label: 'Level', required: true, options: levels },
      { name: 'effective_at', label: '生效时间', type: 'datetime', hint: '留空为立即生效' },
      { name: 'expires_at', label: '到期时间', type: 'datetime', hint: '会员期周期必填' },
      { name: 'renew_term', label: '开始新会期', type: 'checkbox' },
    ],
    save: (t, d) => put(`/tenants/${enc(t)}/subjects/${enc(required(d.subject_id, '用户 ID'))}/level`, {
      level_code: d.level_code, effective_at: d.effective_at, expires_at: d.expires_at, renew_term: d.renew_term,
    }),
  }} />;
}

const DEFAULT_BASE_URL = 'http://127.0.0.1:9460';

type TenantItem = { tenant_id: string; name: string | null; subject_id_definition: string | null };

export function BusinessPage({ tenant, revision, onChanged }: PageProps) {
  const [name, setName] = useState(''), [definition, setDefinition] = useState('');
  const [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => {
    api<{ items: TenantItem[] }>('/tenants').then(r => {
      const item = r.items.find(t => t.tenant_id === tenant);
      setName(item?.name || ''); setDefinition(item?.subject_id_definition || ''); setLoaded(true);
    }).catch(e => setError(e instanceof Error ? e.message : '加载失败'));
  }, [tenant, revision]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      await api(`/tenants/${enc(tenant)}`, 'PUT', { name: name.trim(), subject_id_definition: definition.trim() });
      onChanged('已保存。已签发的接入说明不会自动更新，需要的话请在“客户端凭据”里轮换密钥重新下载');
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); } finally { setBusy(false); }
  }
  return <section className="panel">
    <div className="panel-head"><div><h2>业务系统</h2><p className="muted">业务系统名称和用户 ID 定义。用户 ID 定义会写进本业务系统签发的每一份接入说明。</p></div></div>
    {error && <div className="error-box" role="alert">{error}</div>}
    {loaded && <form onSubmit={save} style={{ display: 'grid', gap: 14, maxWidth: 640 }}>
      <div className="grid two">
        <label>业务系统名称 *<input value={name} required maxLength={100} onChange={e => setName(e.target.value)} /></label>
        <label>系统代码<input value={tenant} disabled /><small className="muted">设置后不能修改</small></label>
      </div>
      <label>用户 ID 定义 *<input value={definition} required maxLength={500} placeholder="例如：user_table.id" onChange={e => setDefinition(e.target.value)} />
        <small className="muted">按业务系统实际情况填写：subject_id 是哪张表的哪个整数字段。会员同步和所有服务请求都必须使用这个 ID。union_id、openid 这类字符串不能直接用，需要先换成整数 ID。</small></label>
      <div className="form-actions"><button disabled={busy}>{busy ? '保存中…' : '保存'}</button></div>
    </form>}
  </section>;
}

export function ClientsPage({ tenant, revision, onChanged }: PageProps) {
  const [offset, setOffset] = useState(0), [form, setForm] = useState<Row | null>(null);
  // The business system's stored user ID definition, prefilled so every credential quotes the same one.
  const [definition, setDefinition] = useState('');
  useEffect(() => {
    api<{ items: { tenant_id: string; subject_id_definition: string | null }[] }>('/tenants')
      .then(r => setDefinition(r.items.find(t => t.tenant_id === tenant)?.subject_id_definition || ''))
      .catch(() => setDefinition(''));
  }, [tenant, revision]);
  const open = (row: Row) => setForm({ ...row, subject_id_definition: definition });
  const [error, setError] = useState('');
  // Nothing in a rotation is editable (the client keeps its Service, role and the business
  // system's definition), so confirm and download straight away.
  async function rotate(row: Row) {
    const id = String(row.client_id);
    if (!window.confirm(`轮换 ${id} 的密钥？\n旧密钥立即失效，使用它的后台需要换上新密钥。确认后会下载新的接入说明。`)) return;
    setError('');
    try {
      await download(`/clients/${enc(id)}/provision`, {
        tenant_id: tenant, role: row.role, rotate: true, base_url: DEFAULT_BASE_URL,
        service_code: row.role === 'membership' ? '' : row.service_code,
      }, `${id}-quotakit.md`);
      onChanged(`已轮换 ${id} 的密钥并下载新的接入说明`);
    } catch (e) { setError(e instanceof Error ? e.message : '轮换失败'); }
  }
  async function remove(row: Row) {
    const id = String(row.client_id);
    const typed = window.prompt(`删除后这把密钥立即失效，使用它的后台会收到 401。\n请输入 Client ID“${id}”确认删除：`);
    if (typed === null) return;
    if (typed.trim() !== id) { setError('输入的 Client ID 不一致，没有删除'); return; }
    setError('');
    try { await api(`/tenants/${enc(tenant)}/clients/${enc(id)}`, 'DELETE'); onChanged(`已删除凭据 ${id}`); }
    catch (e) { setError(e instanceof Error ? e.message : '删除失败'); }
  }
  const result = useTable('tq_clients', tenant, revision, offset);
  const services = useCodes('tq_services', 'service_code', tenant, revision);
  const columns: Column[] = [
    column('client_id'),
    column('service_code', { render: row => row.role === 'membership' ? <span className="muted">不绑定（会员同步）</span> : <span className="code">{text(row.service_code)}</span> }),
    column('role'),
    { key: '_actions', title: '操作', render: row => <Actions items={[
      { label: '轮换密钥', danger: true, action: () => void rotate(row) },
      { label: '删除', danger: true, action: () => void remove(row) },
    ]} /> },
  ];
  const membership = form?.role === 'membership';
  const definitionField: Field = definition
    ? { name: 'subject_id_definition', label: '用户 ID 定义', locked: true, hint: '已在业务系统设定，所有凭据共用。要修改请到“用户与接入 → 业务系统”' }
    : { name: 'subject_id_definition', label: '用户 ID 定义', required: true, placeholder: '例如：user_table.id', hint: '本业务系统还没有设定。按实际情况填写：subject_id 是哪张表的哪个整数字段。之后只能在“业务系统”页修改' };
  // Two forms: a member-sync credential has a fixed role and no Service; a Service credential
  // picks its Service and one of the Service roles.
  const fields: Field[] = membership ? [
    { name: 'client_id', label: 'Client ID', required: true, key: true, placeholder: 'shukang-members' },
    { name: 'role', label: '角色', locked: true, options: [option('membership', '会员同步')], hint: '只能维护会员名单，不调用服务' },
    definitionField,
    { name: 'base_url', label: 'API 地址', required: true, hint: '业务后台和配额服务在同一台生产服务器上时填 http://127.0.0.1:9460。这是那台服务器的本机地址，不是开发人员电脑的 127.0.0.1，接入说明里会写明' },
  ] : [
    { name: 'client_id', label: 'Client ID', required: true, key: true, placeholder: 'wecom-door' },
    { name: 'service_code', label: 'Service', required: true, key: true, options: services },
    { name: 'role', label: '角色', required: true, key: true, options: [option('consumer', '准入+执行'), option('issuer', '准入方'), option('provider', '执行方')] },
    definitionField,
    { name: 'base_url', label: 'API 地址', required: true, hint: '业务后台和配额服务在同一台生产服务器上时填 http://127.0.0.1:9460。这是那台服务器的本机地址，不是开发人员电脑的 127.0.0.1，接入说明里会写明' },
  ];
  return <section className="panel">
    <div className="panel-head"><div><h2>客户端凭据</h2><p className="muted">服务凭据：每个服务签发一份，角色一般选“准入+执行”。会员同步凭据：每个业务系统签发一份，只用来维护会员名单，不绑定服务。签发后浏览器会下载一份接入说明，内含明文密钥。这是唯一一次能拿到明文，请妥善保存，不要提交到 Git。</p></div><div className="toolbar"><button className="secondary" onClick={() => open({ role: 'membership', base_url: DEFAULT_BASE_URL })}>签发会员同步凭据</button><button onClick={() => open({ role: 'consumer', base_url: DEFAULT_BASE_URL })}>签发服务凭据</button></div></div>
    {error && <div className="error-box" role="alert">{error}</div>}
    <Table columns={columns} data={result.rows} loading={result.loading} error={result.error} />
    <Pager offset={offset} total={result.total} onChange={setOffset} />
    {form && <Editor key={membership ? 'membership' : 'service'} title={membership ? '签发会员同步凭据' : '签发服务凭据'} submitLabel="生成并下载" fields={fields} initial={form} editing={false}
      onSave={async d => {
        const id = required(d.client_id, 'Client ID');
        await download(`/clients/${enc(id)}/provision`, { tenant_id: tenant, service_code: d.service_code, role: d.role, base_url: d.base_url, rotate: false, subject_id_definition: d.subject_id_definition }, `${id}-quotakit.md`);
        onChanged(`已为 ${id} 生成密钥并下载接入说明`);
      }}
      onClose={() => setForm(null)} />}
  </section>;
}

const DATA_TABLES: Record<string, { table: string; title: string; description: string; columns: string[] }> = {
  usage: { table: 'tq_usage', title: '用量', description: '每个用户在当前周期已用的额度。', columns: ['subject_id', 'quota_code', 'used_units', 'period_start', 'period_end'] },
  tokens: { table: 'tq_tokens', title: '凭证', description: '每次准入或兑现生成的凭证与扣费状态。', columns: ['id', 'subject_id', 'service_code', 'quota_code', 'status', 'consumed_units', 'session_status', 'admitted_at', 'session_expires_at', 'request_key'] },
  token_items: { table: 'tq_token_items', title: '场次明细', description: '持续凭证的子项资格（序号 0）与每次使用记录。', columns: ['token_id', 'child_service_code', 'slot_no', 'status', 'max_uses', 'used_at', 'request_key'] },
  ledger: { table: 'tq_ledger', title: '流水', description: '扣减与退还的不可变账本。', columns: ['created_at', 'subject_id', 'service_code', 'quota_code', 'event_type', 'delta_units', 'unit_code'] },
};
export const dataPages = Object.keys(DATA_TABLES);

export function DataPage({ kind, tenant, revision }: PageProps & { kind: string }) {
  const spec = DATA_TABLES[kind], [offset, setOffset] = useState(0);
  const result = useTable(spec.table, tenant, revision, offset);
  return <section className="panel">
    <div className="panel-head"><div><h2>{spec.title}</h2><p className="muted">{spec.description} 只读。</p></div></div>
    <Table columns={spec.columns.map(k => column(k))} data={result.rows} loading={result.loading} error={result.error} />
    <Pager offset={offset} total={result.total} onChange={setOffset} />
  </section>;
}

export function Overview({ tenant, revision }: { tenant: string; revision: number }) {
  const counts = [['Service', 'tq_services'], ['Level', 'tq_levels'], ['客户端', 'tq_clients'], ['用户', 'tq_assignments']] as const;
  return <div className="stats">{counts.map(([label, table]) => <Stat key={table} label={label} table={table} tenant={tenant} revision={revision} />)}</div>;
}
function Stat({ label, table, tenant, revision }: { label: string; table: string; tenant: string; revision: number }) {
  const result = useTable(table, tenant, revision, 0, 1);
  return <div className="stat"><span className="muted">{label}</span><strong>{result.loading || result.error ? '—' : result.total}</strong></div>;
}
