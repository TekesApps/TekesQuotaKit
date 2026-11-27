import { useEffect, useState } from 'react';
import { Table } from 'antd';
import { api, enc } from './api';
import { time, type User } from './types';

type Operator = { username: string; role: string; status: string };
type Operation = { recorded_at: string; actor: string; operation: string; target: string; status: number };
type Attempt = { attempted_at: string; username: string; success: boolean };
const roles: Record<string, string> = { system_admin: '系统管理员', security_admin: '安全管理员', auditor: '审计员' };

export function OperatorPanel({ user, logout }: { user: User; logout: () => void }) {
  const [operators, setOperators] = useState<Operator[]>([]), [operations, setOperations] = useState<Operation[]>([]), [attempts, setAttempts] = useState<Attempt[]>([]);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [revision, refresh] = useState(0);
  useEffect(() => {
    let active = true;
    async function load() {
      if (user.role === 'security_admin') {
        const result = await api<{data: Operator[]}>('/operators');
        if (active) setOperators(result.data);
      } else {
        const [ops, logins] = await Promise.all([api<{data: Operation[]}>('/audit/operations'), api<{data: Attempt[]}>('/audit/sign-ins')]);
        if (active) { setOperations(ops.data); setAttempts(logins.data); }
      }
    }
    load().catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [user.role, revision]);
  async function changeStatus(operator: Operator) {
    const status = operator.status === 'active' ? 'disabled' : 'active';
    try {
      await api('/operators/' + enc(operator.username) + '/status', 'PUT', {status});
      setNotice(operator.username + (status === 'active' ? ' 已启用' : ' 已停用'));
      setError(''); refresh(r => r + 1);
    } catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
  }
  async function revoke(username: string) {
    try { await api('/operators/' + enc(username) + '/revoke', 'POST'); setNotice('已注销 ' + username + ' 的会话'); setError(''); }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
  }
  return <main className="main"><header className="topbar"><h1>{roles[user.role]}</h1><div>{user.username} <button onClick={() => refresh(r => r + 1)}>刷新</button> <button onClick={logout}>退出</button></div></header>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {user.role === 'security_admin' ? <section><h2>管理账号</h2><Table rowKey="username" dataSource={operators} columns={[
      {title:'账号', dataIndex:'username'}, {title:'职责',dataIndex:'role',render:r => roles[r] ?? '待登记'}, {title:'状态',dataIndex:'status'},
      {title:'账号管理',render:(_, r) => <button onClick={() => void changeStatus(r)}>{r.status === 'active' ? '停用账号' : '启用账号'}</button>},
      {title:'会话',render:(_, r) => <button onClick={() => void revoke(r.username)}>注销会话</button>},
    ]}/></section> : <><section><h2>管理操作</h2><Table rowKey="id" dataSource={operations} columns={[
      {title:'时间',dataIndex:'recorded_at',render:time}, {title:'操作者',dataIndex:'actor'}, {title:'操作',dataIndex:'operation'}, {title:'对象',dataIndex:'target'}, {title:'结果',dataIndex:'status'},
    ]}/></section><section><h2>登录记录</h2><Table rowKey="id" dataSource={attempts} columns={[
      {title:'时间',dataIndex:'attempted_at',render:time}, {title:'账号',dataIndex:'username'}, {title:'结果',dataIndex:'success', render:s => s ? '成功' : '失败'},
    ]}/></section></>}
  </main>;
}
