import { useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { AutoComplete, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { api, ApiError } from './api';
import type { User } from './types';
import { Sidebar, type NavigationGroup } from './sidebar';
import { AssignmentsPage, ClientsPage, DataPage, LevelsPage, LimitsPage, MembersPage, Overview, QuotasPage, ServicesPage, dataPages, type PageProps } from './pages';
import './admin.css';
import './login.css';

const navigation: NavigationGroup[] = [
  { label: '规则配置', items: [['services', 'Service 服务'], ['members', '组合成员'], ['quotas', 'Quota 额度'], ['levels', 'Level 等级'], ['limits', '额度规则']] },
  { label: '用户与接入', items: [['assignments', '用户等级'], ['clients', '客户端凭据']] },
  { label: '运行数据', items: [['usage', '用量'], ['tokens', '凭证'], ['token_items', '场次明细'], ['ledger', '流水']] },
];
const TENANT_KEY = 'tq-admin-tenant';
const SECTION_KEY = 'tq-admin-section';
const remembered = (key: string, fallback: string) => { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } };
const remember = (key: string, value: string) => { try { localStorage.setItem(key, value); } catch { /* private mode */ } };

function Login({ onLogin, initialError }: { onLogin: (user: User) => void; initialError: string }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(initialError);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const data = Object.fromEntries(new FormData(event.currentTarget));
    try { onLogin((await api<{ data: User }>('/session/login', 'POST', data)).data); }
    catch (e) { setError(e instanceof Error ? e.message : '登录失败'); } finally { setBusy(false); }
  }
  return <main className="login">
    <section className="login-story"><div className="wordmark">✳ TekesQuotaKit <span>ADMIN</span></div><div><p className="eyebrow">SERVICE QUOTA OPERATIONS</p><h1>让每一次服务，<br/>有据可依。</h1><p>服务、额度、会员等级与接入凭据的统一管理工作台。</p></div><small>TekesQuotaKit 配额管理</small></section>
    <section className="login-panel"><form onSubmit={submit}><div className="tag">管理人员入口</div><h2>登录工作台</h2><p className="muted">使用配额管理账号登录</p>{error && <p role="alert" className="error">{error}</p>}<label>账号<input name="username" required autoComplete="username" maxLength={100} /></label><label>密码<input name="password" type="password" required autoComplete="current-password" maxLength={128} /></label><button className="primary" disabled={busy}>{busy ? '正在登录…' : '登录管理平台 →'}</button></form></section>
  </main>;
}

function TenantPicker({ value, onChange, revision }: { value: string; onChange: (tenant: string) => void; revision: number }) {
  const [options, setOptions] = useState<string[]>([]), [draft, setDraft] = useState(value);
  useEffect(() => { api<{ tenants: string[] }>('/tenants').then(r => setOptions(r.tenants)).catch(() => setOptions([])); }, [revision]);
  useEffect(() => setDraft(value), [value]);
  // Switch only on Enter or a picked option; leaving the field restores the current tenant.
  const commit = (next: string) => { const tenant = next.trim(); if (tenant && tenant !== value) onChange(tenant); else setDraft(value); };
  return <AutoComplete aria-label="租户" style={{ width: 200 }} value={draft} placeholder="租户，如 demo-tenant"
    options={options.map(t => ({ value: t }))} onChange={setDraft} onSelect={commit} onBlur={() => setDraft(value)}
    onKeyDown={e => { if (e.key === 'Enter') commit(draft); if (e.key === 'Escape') setDraft(value); }} />;
}

function App() {
  const [user, setUser] = useState<User | null>(null), [initializing, setInitializing] = useState(true);
  const [section, setSection] = useState(() => remembered(SECTION_KEY, 'services'));
  const [tenant, setTenant] = useState(() => remembered(TENANT_KEY, ''));
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [revision, setRevision] = useState(0), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => {
    api<{ data: User }>('/session').then(r => setUser(r.data)).catch(e => {
      if (!(e instanceof ApiError && e.status === 401)) setError(e.message);
    }).finally(() => setInitializing(false));
    const expired = () => { setUser(null); setNotice(''); setError('登录已失效，请重新登录'); };
    window.addEventListener('admin-session-expired', expired);
    return () => window.removeEventListener('admin-session-expired', expired);
  }, []);
  useEffect(() => {
    if (!user || tenant) return;
    api<{ tenants: string[] }>('/tenants').then(r => { if (r.tenants[0]) changeTenant(r.tenants[0]); }).catch(() => undefined);
  }, [user, tenant]);
  function changeTenant(next: string) { setTenant(next); remember(TENANT_KEY, next); setNotice(''); setError(''); }
  function go(next: string) { setSection(next); remember(SECTION_KEY, next); setNotice(''); setError(''); }
  async function logout() {
    setBusy(true); setError('');
    try { await api('/session/logout', 'POST'); setUser(null); setNotice(''); }
    catch (e) { setError(e instanceof Error ? e.message : '退出失败'); } finally { setBusy(false); }
  }
  function changed(message: string) { setNotice(message); setRevision(r => r + 1); }
  if (initializing) return <div className="loading">正在连接配额管理平台…</div>;
  if (!user) return <Login initialError={error} onLogin={u => { setUser(u); setError(''); }} />;
  const title = navigation.flatMap(group => group.items).find(([key]) => key === section)?.[1] || '配额管理';
  const props: PageProps = { tenant, revision, onChanged: changed };
  const name = user.username || '管理员密钥';
  return <div className={`admin-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}><Sidebar groups={navigation} section={section} onCollapse={setSidebarCollapsed} onNavigate={go} />
    <div className="workspace"><header className="workspace-header"><span>配额管理 <span className="muted"> / {title}</span></span><div><TenantPicker value={tenant} onChange={changeTenant} revision={revision} /><span className="avatar">{name.slice(0, 1).toUpperCase()}</span>{name}<button className="ghost" disabled={busy} onClick={() => void logout()}>退出</button></div></header>
      <main className="main"><div className="topbar"><div><p className="eyebrow">TENANT · {tenant || '未选择'}</p><h1>{title}</h1></div><button className="secondary" onClick={() => { setRevision(r => r + 1); setNotice(''); }}>刷新</button></div>
        {error && <div className="error-box" role="alert">{error}</div>}{notice && <p className="notice" role="status">{notice}</p>}
        {!tenant ? <section className="panel"><h2>请选择租户</h2><p className="muted">在右上角输入或选择租户。新租户输入名称后按回车，即可开始配置。</p></section> : <>
          <Overview tenant={tenant} revision={revision} />
          <div key={`${section}:${tenant}`}>
            {section === 'services' && <ServicesPage {...props} />}
            {section === 'members' && <MembersPage {...props} />}
            {section === 'quotas' && <QuotasPage {...props} />}
            {section === 'levels' && <LevelsPage {...props} />}
            {section === 'limits' && <LimitsPage {...props} />}
            {section === 'assignments' && <AssignmentsPage {...props} />}
            {section === 'clients' && <ClientsPage {...props} />}
            {dataPages.includes(section) && <DataPage {...props} kind={section} />}
          </div>
        </>}
      </main></div></div>;
}

createRoot(document.getElementById('root')!).render(<ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#18775f', fontSize: 16, borderRadius: 7, controlHeight: 38, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' } }}><App /></ConfigProvider>);
