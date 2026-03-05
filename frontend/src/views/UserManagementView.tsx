import { useState, useEffect, useCallback } from 'react';
import { Users, CheckCircle, XCircle, RefreshCw, Loader2, ShieldCheck, UserCheck } from 'lucide-react';
import request from '@/utils/request';
import type { ApiResponse } from '@/types/api';
import type { CurrentUser } from '@/types/user';

// ── API ──────────────────────────────────────────────────────────────────────

interface UserItem extends CurrentUser {
    id: string;
}

const fetchAllUsers = (): Promise<ApiResponse<UserItem[]>> =>
    request({ url: '/users', method: 'get' });

const verifyUser = (userId: string): Promise<ApiResponse<UserItem>> =>
    request({ url: `/users/${userId}/verify`, method: 'post' });

const setUserStatus = (userId: string, isActive: boolean): Promise<ApiResponse<unknown>> =>
    request({ url: `/admin/users/${userId}/status`, method: 'post', data: { is_active: isActive } });

// ── Role Badge ───────────────────────────────────────────────────────────────

function RoleBadge({ role }: { role: string }) {
    const colorMap: Record<string, { color: string; bg: string }> = {
        admin: { color: '#f87171', bg: '#f8717120' },
        developer: { color: '#60a5fa', bg: '#60a5fa20' },
        viewer: { color: '#4ade80', bg: '#4ade8020' },
    };
    const c = colorMap[role] ?? { color: '#94a3b8', bg: '#94a3b820' };
    return (
        <span style={{
            color: c.color, background: c.bg,
            border: `1px solid ${c.color}44`,
            borderRadius: '4px', padding: '2px 8px',
            fontSize: '0.72rem', fontWeight: 600, letterSpacing: '0.04em',
        }}>
            {role.toUpperCase()}
        </span>
    );
}

// ── StatusPill ────────────────────────────────────────────────────────────────

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: '4px',
            color: ok ? '#4ade80' : '#f87171',
            fontSize: '0.78rem', fontWeight: 500,
        }}>
            {ok ? <CheckCircle size={13} /> : <XCircle size={13} />}
            {label}
        </span>
    );
}

// ── Main View ────────────────────────────────────────────────────────────────

export default function UserManagementView() {
    const [users, setUsers] = useState<UserItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [actionId, setActionId] = useState<string | null>(null); // 正在操作的 userId

    const loadUsers = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetchAllUsers();
            if (res?.data) setUsers(res.data);
        } catch {
            // request.ts 会处理 401
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadUsers(); }, [loadUsers]);

    const handleVerify = async (userId: string) => {
        setActionId(userId);
        try {
            const res = await verifyUser(userId);
            if (res?.data) {
                setUsers(prev => prev.map(u => u.id === userId ? { ...u, is_verified: true } : u));
            }
        } catch {
            alert('操作失败，请检查权限');
        } finally {
            setActionId(null);
        }
    };

    const handleToggleStatus = async (user: UserItem) => {
        setActionId(user.id);
        const next = !user.is_active;
        try {
            await setUserStatus(user.id, next);
            setUsers(prev => prev.map(u => u.id === user.id ? { ...u, is_active: next } : u));
        } catch {
            alert('操作失败，请检查权限');
        } finally {
            setActionId(null);
        }
    };

    return (
        <div className="al-container">
            {/* 标题栏 */}
            <div className="al-toolbar glass-panel">
                <div className="al-toolbar-left">
                    <Users size={20} />
                    <h2>用户管理</h2>
                    <span className="al-readonly-badge">Admin</span>
                </div>
                <button className="al-btn al-btn-ghost" onClick={loadUsers} disabled={loading}>
                    <RefreshCw size={14} className={loading ? 'al-spin' : ''} />
                    刷新
                </button>
            </div>

            {/* 表格 */}
            <div className="al-table-wrap glass-panel">
                {loading && users.length === 0 ? (
                    <div className="al-empty">
                        <Loader2 size={28} className="al-spin" />
                        <p>加载中...</p>
                    </div>
                ) : users.length === 0 ? (
                    <div className="al-empty">
                        <Users size={44} strokeWidth={1} />
                        <p>暂无用户数据</p>
                    </div>
                ) : (
                    <table className="al-table">
                        <thead>
                            <tr>
                                <th>邮箱</th>
                                <th>角色</th>
                                <th>激活状态</th>
                                <th>邮箱验证</th>
                                <th>超级管理员</th>
                                <th style={{ textAlign: 'center' }}>操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {users.map(user => (
                                <tr key={user.id}>
                                    <td style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>
                                        {user.email}
                                    </td>
                                    <td><RoleBadge role={user.role} /></td>
                                    <td><StatusPill ok={user.is_active} label={user.is_active ? '已激活' : '已禁用'} /></td>
                                    <td><StatusPill ok={user.is_verified} label={user.is_verified ? '已验证' : '未验证'} /></td>
                                    <td><StatusPill ok={user.is_superuser} label={user.is_superuser ? '是' : '否'} /></td>
                                    <td>
                                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'center' }}>
                                            {/* 验证按钮：仅未验证时显示 */}
                                            {!user.is_verified && (
                                                <button
                                                    className="al-btn al-btn-primary"
                                                    style={{ padding: '4px 10px', fontSize: '0.75rem' }}
                                                    onClick={() => handleVerify(user.id)}
                                                    disabled={actionId === user.id}
                                                    title="将该用户设为已验证"
                                                >
                                                    {actionId === user.id
                                                        ? <Loader2 size={12} className="al-spin" />
                                                        : <><UserCheck size={12} /> 验证</>
                                                    }
                                                </button>
                                            )}

                                            {/* 激活/禁用按钮 */}
                                            <button
                                                className="al-btn al-btn-ghost"
                                                style={{
                                                    padding: '4px 10px', fontSize: '0.75rem',
                                                    color: user.is_active ? '#f87171' : '#4ade80',
                                                }}
                                                onClick={() => handleToggleStatus(user)}
                                                disabled={actionId === user.id}
                                                title={user.is_active ? '禁用该用户' : '启用该用户'}
                                            >
                                                {actionId === user.id
                                                    ? <Loader2 size={12} className="al-spin" />
                                                    : user.is_active
                                                        ? <><XCircle size={12} /> 禁用</>
                                                        : <><CheckCircle size={12} /> 启用</>
                                                }
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            <div className="al-readonly-notice">
                <ShieldCheck size={14} />
                此页面仅供 Admin 使用，操作将立即生效。禁用用户后，该用户的下次请求将被拒绝（401）。
            </div>
        </div>
    );
}
