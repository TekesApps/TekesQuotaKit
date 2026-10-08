import { useEffect, useState } from 'react';

// Collapsible sidebar and navigation sections, following skzy-admin's sidebar (v0.2.4–v0.2.5).
export type NavigationGroup = { label: string; standalone?: boolean; items: string[][] };

type Props = { groups: NavigationGroup[]; section: string; version?: string; onNavigate: (section: string) => void; onCollapse: (collapsed: boolean) => void };

const SIDEBAR_KEY = 'tq-admin-sidebar-collapsed';
const GROUPS_KEY = 'tq-admin-nav-collapsed-groups';

function savedCollapsedGroups(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(GROUPS_KEY) || '[]');
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
  } catch { return []; }
}

export function Sidebar({ groups, section, version, onNavigate, onCollapse }: Props) {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(SIDEBAR_KEY) === 'true'; } catch { return false; }
  });
  const [collapsedGroups, setCollapsedGroups] = useState(savedCollapsedGroups);
  useEffect(() => {
    onCollapse(collapsed);
    try { localStorage.setItem(SIDEBAR_KEY, String(collapsed)); } catch { /* Storage is optional. */ }
  }, [collapsed, onCollapse]);
  useEffect(() => {
    try { localStorage.setItem(GROUPS_KEY, JSON.stringify(collapsedGroups)); } catch { /* Storage is optional. */ }
  }, [collapsedGroups]);
  const toggleGroup = (label: string) => setCollapsedGroups(current => current.includes(label) ? current.filter(item => item !== label) : [...current, label]);
  return <aside className={`sidebar ${collapsed ? 'is-collapsed' : ''}`} aria-label="管理导航">
    <div className="sidebar-heading"><div className="wordmark"><span aria-hidden="true">✳</span><span className="sidebar-name"> TekesQuotaKit</span></div><button className="sidebar-toggle" type="button" aria-label={collapsed ? '展开侧栏' : '收起侧栏'} title={collapsed ? '展开侧栏' : '收起侧栏'} aria-expanded={!collapsed} aria-controls="admin-sidebar-navigation" onClick={() => setCollapsed(value => !value)}><svg className="sidebar-arrow" style={{ transform: collapsed ? 'rotate(180deg)' : undefined }} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 6 6 12l6 6M18 6l-6 6 6 6" /></svg></button></div>
    <div className="aside-caption" hidden={collapsed}>配额管理平台{version && <>{' '}<span className="app-version">v{version}</span></>}</div>
    <nav id="admin-sidebar-navigation" hidden={collapsed}>{groups.map(group => {
      const expanded = !collapsedGroups.includes(group.label);
      const contentId = `nav-section-${group.items[0][0]}`;
      return <div className="nav-group" key={group.label}>
        {!group.standalone && <button className="nav-section-toggle" type="button" aria-expanded={expanded} aria-controls={contentId} onClick={() => toggleGroup(group.label)}><span>{group.label}</span><svg className={`section-chevron ${expanded ? 'expanded' : ''}`} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6" /></svg></button>}
        <div className="nav-items" id={contentId} hidden={!group.standalone && !expanded}>{group.items.map(([key, label]) => <button key={key} type="button" aria-current={section === key ? 'page' : undefined} className={`nav-btn ${group.standalone ? 'top-level' : 'sub'} ${section === key ? 'active' : ''}`} onClick={() => onNavigate(key)}>{label}</button>)}</div>
      </div>;
    })}</nav>
  </aside>;
}
