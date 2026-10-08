import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Dropdown, Select } from 'antd';
import { api, enc } from './api';
import { type Row, statusNames, text } from './types';

export type Column = { title: string; key: string; render?: (row: Row) => ReactNode };
export type Option = { value: string; label: string };
export type Field = {
  name: string; label: string; required?: boolean; key?: boolean;
  type?: 'text' | 'number' | 'checkbox' | 'datetime';
  options?: Option[]; placeholder?: string; hint?: string; min?: number; max?: number;
};

export function Status({ value }: { value: unknown }) {
  const ok = ['active', 'settled', 'open', 'used', 'consume', 'finite', 'unlimited'].includes(String(value));
  return <span className={`pill ${ok ? 'ok' : ''}`}>{statusNames[String(value)] || text(value)}</span>;
}

export function Actions({ items, busy = false }: { items: { label: string; action: () => void; danger?: boolean }[]; busy?: boolean }) {
  if (!items.length) return <span className="muted">只读</span>;
  return <Dropdown trigger={['click']} menu={{ items: items.map((item, index) => ({ key: String(index), label: item.label, danger: item.danger, disabled: busy, onClick: item.action })) }}>
    <button type="button" disabled={busy} className="secondary" aria-label="操作菜单">操作 ▾</button>
  </Dropdown>;
}

export function Table({ columns, data, loading, error }: { columns: Column[]; data: Row[]; loading?: boolean; error?: string }) {
  return <div className="table-wrap"><table><thead><tr>{columns.map(c => <th key={c.key}>{c.title}</th>)}</tr></thead><tbody>
    {loading || error || !data.length
      ? <tr><td colSpan={columns.length} className={error ? 'error' : 'muted'}>{loading ? '正在加载…' : error || '暂无记录'}</td></tr>
      : data.map((row, index) => <tr key={String(row.id ?? index)}>{columns.map(c => <td key={c.key}>{c.render ? c.render(row) : text(row[c.key])}</td>)}</tr>)}
  </tbody></table></div>;
}

type TablePage = { columns: string[]; total: number; offset: number; rows: Row[] };
export const PAGE_SIZE = 50;

/** Reads one tq_ table for a tenant through the admin browse endpoint, newest first. */
export function useTable(table: string, tenant: string, revision: number, offset = 0, limit = PAGE_SIZE) {
  const [data, setData] = useState<TablePage | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    if (!tenant) { setData(null); setLoading(false); return; }
    api<TablePage>(`/tables/${table}?tenant=${enc(tenant)}&limit=${limit}&offset=${offset}`)
      .then(r => { if (active) setData(r); })
      .catch(e => { if (active) { setData(null); setError(e instanceof Error ? e.message : '加载失败'); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [table, tenant, revision, offset, limit]);
  return { rows: data?.rows ?? [], total: data?.total ?? 0, loading, error };
}

/** Codes from a config table, for select options in editors. */
export function useCodes(table: string, column: string, tenant: string, revision: number): Option[] {
  const { rows } = useTable(table, tenant, revision, 0, 200);
  return rows.map(r => String(r[column])).sort().map(value => ({ value, label: value }));
}

export function Pager({ offset, total, onChange }: { offset: number; total: number; onChange: (offset: number) => void }) {
  if (total <= PAGE_SIZE) return null;
  const page = Math.floor(offset / PAGE_SIZE) + 1, pages = Math.ceil(total / PAGE_SIZE);
  return <div className="pagination toolbar">
    <button className="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}>上一页</button>
    <span className="muted">第 {page} / {pages} 页 · 共 {total} 条</span>
    <button className="secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => onChange(offset + PAGE_SIZE)}>下一页</button>
  </div>;
}

export function Drawer({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const root = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    // Focus the first editable field, not the drawer's close button.
    (root.current?.querySelector<HTMLElement>('form input:not(:disabled), form .ant-select:not(.ant-select-disabled) input') ?? root.current?.querySelector<HTMLElement>('button'))?.focus();
    const key = (e: KeyboardEvent) => {
      // Escape first closes an open dropdown; only a second Escape closes the drawer.
      if (e.key === 'Escape' && !document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')) close.current();
      if (e.key === 'Tab') {
        const controls = Array.from(root.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]') || []);
        const target = e.shiftKey ? controls[controls.length - 1] : controls[0];
        if ((e.shiftKey && document.activeElement === controls[0]) || (!e.shiftKey && document.activeElement === controls[controls.length - 1])) { e.preventDefault(); target?.focus(); }
      }
    };
    document.addEventListener('keydown', key);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', key); document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  return <div className="drawer" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}><div ref={root} className="drawer-panel" role="dialog" aria-modal="true" aria-label={title}><div className="drawer-head"><h2>{title}</h2><button type="button" className="secondary" onClick={onClose}>关闭</button></div>{children}</div></div>;
}

// datetime-local works in the browser's zone; the API takes ISO 8601 with an offset.
const toLocalInput = (value: unknown) => {
  if (!value) return '';
  const date = new Date(String(value).endsWith('Z') ? String(value) : `${value}Z`);
  if (Number.isNaN(date.getTime())) return '';
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const fromLocalInput = (value: string) => value ? new Date(value).toISOString() : null;

export function Editor({ title, fields, initial = {}, editing = false, submitLabel = '保存', onSave, onClose, children }: {
  title: string; fields: Field[]; initial?: Row; editing?: boolean; submitLabel?: string;
  onSave: (data: Row) => Promise<void>; onClose: () => void; children?: ReactNode;
}) {
  const [values, setValues] = useState<Row>(() => Object.fromEntries(fields.map(f => [f.name, f.type === 'datetime' ? toLocalInput(initial[f.name]) : initial[f.name] ?? (f.type === 'checkbox' ? false : '')])));
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const set = (name: string, value: unknown) => setValues(v => ({ ...v, [name]: value }));
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    const data: Row = {};
    for (const f of fields) {
      const value = values[f.name];
      data[f.name] = f.type === 'number' ? (value === '' || value == null ? null : Number(value))
        : f.type === 'datetime' ? fromLocalInput(String(value || ''))
        : f.type === 'checkbox' ? Boolean(value)
        : typeof value === 'string' ? value.trim() : value;
    }
    try { await onSave(data); onClose(); } catch (err) { setError(err instanceof Error ? err.message : '保存失败'); } finally { setBusy(false); }
  }
  return <Drawer title={title} onClose={() => { if (!busy) onClose(); }}><form onSubmit={save}>
    {error && <div role="alert" className="error-box">{error}</div>}
    <div className="grid two">{fields.map(field => {
      const locked = editing && field.key;
      const value = values[field.name];
      const control = field.options
        ? <Select aria-label={field.label} disabled={locked} showSearch optionFilterProp="label" allowClear={!field.required} placeholder={field.placeholder || '请选择'} options={field.options} value={value === '' || value == null ? undefined : String(value)} onChange={v => set(field.name, v ?? '')} />
        : field.type === 'checkbox'
          ? <input type="checkbox" style={{ width: 'auto', minHeight: 0 }} checked={Boolean(value)} onChange={e => set(field.name, e.target.checked)} />
          : <input type={field.type === 'datetime' ? 'datetime-local' : field.type || 'text'} disabled={locked} value={String(value ?? '')} required={field.required} min={field.min} max={field.max} placeholder={field.placeholder} onChange={e => set(field.name, e.target.value)} />;
      return <label key={field.name}>{field.label}{field.required && !locked ? ' *' : ''}{control}{field.hint && <small className="muted">{field.hint}</small>}</label>;
    })}</div>
    {children}<div className="form-actions"><button disabled={busy}>{busy ? '保存中…' : submitLabel}</button><button type="button" disabled={busy} className="secondary" onClick={onClose}>取消</button></div>
  </form></Drawer>;
}
