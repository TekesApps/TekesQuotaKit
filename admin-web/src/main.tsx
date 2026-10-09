import { useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider, Select } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { api, ApiError } from './api';
import type { Row, User } from './types';
import { Sidebar, type NavigationGroup } from './sidebar';
import { AssignmentsPage, BusinessPage, ClientsPage, DataPage, LevelsPage, LimitsPage, MembersPage, Overview, QuotasPage, ServicesPage, dataPages, type PageProps } from './pages';
import './admin.css';
import './login.css';
import './antd-overrides.css';

const navigation: NavigationGroup[] = [
  { label: '规则配置', items: [['services', 'Service 服务'], ['members', '组合成员'], ['quotas', 'Quota 配额'], ['levels', 'Level 等级'], ['limits', '等级额度']] },
  { label: '用户与接入', items: [['business', '业务系统'], ['assignments', '用户等级'], ['clients', '客户端凭据']] },
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
    <section className="login-story"><div className="wordmark">✳ TekesQuotaKit <span>ADMIN</span></div><div><p className="eyebrow">SERVICE QUOTA OPERATIONS</p><h1>让每一次服务，<br/>有据可依。</h1><p>服务、配额、会员等级与接入凭据的统一管理工作台。</p></div><small>TekesQuotaKit 配额管理</small></section>
    <section className="login-panel"><form onSubmit={submit}><div className="tag">管理人员入口</div><h2>登录工作台</h2><p className="muted">使用配额管理账号登录</p>{error && <p role="alert" className="error">{error}</p>}<label>账号<input name="username" required autoComplete="username" maxLength={100} /></label><label>密码<input name="password" type="password" required autoComplete="current-password" maxLength={128} /></label><button className="primary" disabled={busy}>{busy ? '正在登录…' : '登录管理平台 →'}</button></form></section>
  </main>;
}

type TenantItem = { tenant_id: string; name: string | null; registered: boolean };

function BusinessSetup({ suggestion, onDone }: { suggestion: string; onDone: (tenant: string) => void }) {
  const [name, setName] = useState(''), [code, setCode] = useState(suggestion), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const tenant = code.trim();
    try { await api(`/tenants/${encodeURIComponent(tenant)}`, 'PUT', { name: name.trim() }); onDone(tenant); }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败'); } finally { setBusy(false); }
  }
  return <section className="panel"><form onSubmit={submit} style={{ maxWidth: 560, display: 'grid', gap: 14 }}>
    <div><h2>设置业务系统</h2><p className="muted">首次使用，请先填写接入配额管理的业务系统。之后的所有规则和客户端凭据都属于这个业务系统。</p></div>
    {error && <div className="error-box" role="alert">{error}</div>}
    <label>业务系统名称 *<input value={name} required maxLength={100} placeholder="数康智医" onChange={e => setName(e.target.value)} /></label>
    <label>系统代码 *<input value={code} required maxLength={64} pattern="[A-Za-z0-9._\-]+" placeholder="shukang-zhiyi" onChange={e => setCode(e.target.value)} />
      <small className="muted">{suggestion && code === suggestion ? '已有数据使用这个代码，沿用它即可接上已有规则。' : '只能用英文字母、数字和 . _ -。设置后不能修改，规则和凭据都绑定在它上面。'}</small></label>
    <div className="form-actions"><button disabled={busy}>{busy ? '保存中…' : '开始使用'}</button></div>
  </form></section>;
}

function TenantSwitch({ items, value, onChange }: { items: TenantItem[]; value: string; onChange: (tenant: string) => void }) {
  const current = items.find(t => t.tenant_id === value);
  if (items.length <= 1) return <span title={value} style={{ fontWeight: 650, marginRight: 8 }}>{current?.name || value}</span>;
  return <Select aria-label="业务系统" style={{ width: 220 }} value={value} onChange={onChange}
    options={items.map(t => ({ value: t.tenant_id, label: `${t.name}（${t.tenant_id}）` }))} />;
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
  const [tenants, setTenants] = useState<TenantItem[] | null>(null);
  useEffect(() => {
    if (!user) { setTenants(null); return; }
    api<{ items: TenantItem[] }>('/tenants').then(r => setTenants(r.items)).catch(e => { setTenants([]); setError(e instanceof Error ? e.message : '业务系统加载失败'); });
  }, [user, revision]);
  const registered = (tenants ?? []).filter(t => t.registered);
  useEffect(() => {
    // Single business system in practice: keep the remembered one if still registered, else the first.
    if (tenants && !registered.some(t => t.tenant_id === tenant)) changeTenant(registered[0]?.tenant_id ?? '');
  }, [tenants]); // eslint-disable-line react-hooks/exhaustive-deps
  function changeTenant(next: string) { setTenant(next); remember(TENANT_KEY, next); setNotice(''); setError(''); }
  const [focus, setFocus] = useState<Row | undefined>();
  function go(next: string, nextFocus?: Row) { setSection(next); setFocus(nextFocus); remember(SECTION_KEY, next); setNotice(''); setError(''); }
  async function logout() {
    setBusy(true); setError('');
    try { await api('/session/logout', 'POST'); setUser(null); setNotice(''); }
    catch (e) { setError(e instanceof Error ? e.message : '退出失败'); } finally { setBusy(false); }
  }
  function changed(message: string) { setNotice(message); setRevision(r => r + 1); }
  if (initializing) return <div className="loading">正在连接配额管理平台…</div>;
  if (!user) return <Login initialError={error} onLogin={u => { setUser(u); setError(''); }} />;
  const title = tenants && !tenant ? '设置业务系统' : navigation.flatMap(group => group.items).find(([key]) => key === section)?.[1] || '配额管理';
  const props: PageProps = { tenant, revision, onChanged: changed, focus, onNavigate: go };
  const name = user.username || '管理员密钥';
  return <div className={`admin-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}><Sidebar groups={navigation} section={section} version={user.version} onCollapse={setSidebarCollapsed} onNavigate={key => go(key)} />
    <div className="workspace"><header className="workspace-header"><span>配额管理 <span className="muted"> / {title}</span></span><div>{tenant && <TenantSwitch items={registered} value={tenant} onChange={changeTenant} />}<span className="avatar">{name.slice(0, 1).toUpperCase()}</span>{name}<button className="ghost" disabled={busy} onClick={() => void logout()}>退出</button></div></header>
      <main className="main"><div className="topbar"><div><p className="eyebrow">{tenant ? `BUSINESS · ${tenant}` : 'SETUP'}</p><h1>{title}</h1></div><button className="secondary" onClick={() => { setRevision(r => r + 1); setNotice(''); }}>刷新</button></div>
        {error && <div className="error-box" role="alert">{error}</div>}{notice && <p className="notice" role="status">{notice}</p>}
        {tenants === null ? <div className="loading">正在加载…</div> : !tenant ? <BusinessSetup
          suggestion={(tenants.length === 1 && !tenants[0].registered) ? tenants[0].tenant_id : ''}
          onDone={code => { setTenants(current => [...(current ?? []).filter(t => t.tenant_id !== code), { tenant_id: code, name: '', registered: true }]); changeTenant(code); changed('业务系统已设置'); api<{ items: TenantItem[] }>('/tenants').then(r => setTenants(r.items)).catch(() => undefined); }} /> : <>
          <Overview tenant={tenant} revision={revision} />
          <div key={`${section}:${tenant}:${JSON.stringify(focus ?? {})}`}>
            {section === 'services' && <ServicesPage {...props} />}
            {section === 'members' && <MembersPage {...props} />}
            {section === 'quotas' && <QuotasPage {...props} />}
            {section === 'levels' && <LevelsPage {...props} />}
            {section === 'limits' && <LimitsPage {...props} />}
            {section === 'assignments' && <AssignmentsPage {...props} />}
            {section === 'clients' && <ClientsPage {...props} />}
            {section === 'business' && <BusinessPage {...props} />}
            {dataPages.includes(section) && <DataPage {...props} kind={section} />}
          </div>
        </>}
      </main></div></div>;
}

createRoot(document.getElementById('root')!).render(<ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#18775f', fontSize: 12.8, lineHeight: 1.5, fontSizeSM: 11.2, fontSizeLG: 14.4, fontSizeXL: 16, borderRadius: 7, controlHeight: 38, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' } }}><App /></ConfigProvider>);
