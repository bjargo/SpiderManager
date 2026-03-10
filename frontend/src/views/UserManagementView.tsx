import { useState, useEffect, useCallback } from 'react';
import { Users, CheckCircle, XCircle, RefreshCw, Loader2, ShieldCheck, UserCheck, UserPlus, Copy, Check, X } from 'lucide-react';
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

interface CreateUserPayload {
    email: string;
    role: 'admin' | 'developer' | 'viewer';
    is_verified?: boolean;
}

interface CreateUserResult {
    id: string;
    email: string;
    role: string;
    initial_password: string;
}

const createUser = (data: CreateUserPayload): Promise<ApiResponse<CreateUserResult>> =>
    request({ url: '/admin/users', method: 'post', data });

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
    const [actionId, setActionId] = useState<string | null>(null);
    const [showCreateModal, setShowCreateModal] = useState(false);
    const [createEmail, setCreateEmail] = useState('');
    const [createRole, setCreateRole] = useState<'admin' | 'developer' | 'viewer'>('developer');
    const [createVerified, setCreateVerified] = useState(true);
    const [createLoading, setCreateLoading] = useState(false);
    const [createdUser, setCreatedUser] = useState<CreateUserResult | null>(null);
    const [copied, setCopied] = useState(false);

    const loadUsers = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetchAllUsers();
            if (res?.data) setUsers(res.data);
        } catch {
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

    const handleCreateUser = async () => {
        if (!createEmail.trim()) {
            alert('请输入邮箱地址');
            return;
        }
        setCreateLoading(true);
        try {
            const res = await createUser({
                email: createEmail.trim(),
                role: createRole,
                is_verified: createVerified,
            });
            if (res?.data) {
                setCreatedUser(res.data);
                await loadUsers();
            }
        } catch {
            alert('创建用户失败，请检查邮箱格式或是否已存在');
        } finally {
            setCreateLoading(false);
        }
    };

    const handleCopyPassword = () => {
        if (createdUser?.initial_password) {
            navigator.clipboard.writeText(createdUser.initial_password);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        }
    };

    const handleCloseCreateModal = () => {
        setShowCreateModal(false);
        setCreateEmail('');
        setCreateRole('developer');
        setCreateVerified(true);
        setCreatedUser(null);
        setCopied(false);
    };

    return (
        <div className="al-container animate-fade-in">
            {/* 标题栏 */}
            <div className="al-toolbar glass-panel" style={{ marginBottom: '20px' }}>
                <div className="al-toolbar-left">
                    <div style={{
                        background: 'var(--accent-glow)',
                        padding: '8px',
                        borderRadius: '10px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        color: 'var(--accent-primary)'
                    }}>
                        <Users size={20} />
                    </div>
                    <div style={{ marginLeft: '4px' }}>
                        <h2 style={{ fontSize: '1.25rem', fontWeight: 700, letterSpacing: '-0.02em' }}>用户管理</h2>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '2px' }}>
                            <ShieldCheck size={12} style={{ color: 'var(--text-muted)' }} />
                            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 500 }}>仅管理员可用</span>
                        </div>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: '10px' }}>
                    <button
                        className="al-btn-base al-btn-primary"
                        onClick={() => setShowCreateModal(true)}
                        style={{ background: 'var(--accent-primary)', color: 'white', border: 'none' }}
                    >
                        <UserPlus size={16} />
                        <span>创建新用户</span>
                    </button>
                    <button
                        className="al-btn-base al-btn-ghost"
                        onClick={loadUsers}
                        disabled={loading}
                        style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}
                    >
                        <RefreshCw size={16} className={loading ? 'al-spin' : ''} />
                    </button>
                </div>
            </div>

            {/* 表格 */}
            <div className="al-table-wrap glass-panel animate-slide-up" style={{ padding: '0', overflow: 'hidden' }}>
                {loading && users.length === 0 ? (
                    <div className="al-empty" style={{ padding: '60px 0' }}>
                        <Loader2 size={32} className="al-spin" style={{ color: 'var(--accent-primary)' }} />
                        <p style={{ marginTop: '12px', color: 'var(--text-secondary)' }}>加载中...</p>
                    </div>
                ) : users.length === 0 ? (
                    <div className="al-empty" style={{ padding: '60px 0' }}>
                        <Users size={48} strokeWidth={1} style={{ color: 'var(--text-muted)', opacity: 0.5 }} />
                        <p style={{ marginTop: '12px', color: 'var(--text-secondary)' }}>暂无用户数据</p>
                    </div>
                ) : (
                    <table className="al-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                            <tr style={{ background: 'var(--bg-tertiary)', borderBottom: '1px solid var(--border-color)' }}>
                                <th style={{ textAlign: 'center', padding: '14px 20px', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>邮箱</th>
                                <th style={{ textAlign: 'center', padding: '14px 20px', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>角色</th>
                                <th style={{ textAlign: 'center', padding: '14px 20px', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>状态</th>
                                <th style={{ textAlign: 'center', padding: '14px 20px', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>验证</th>
                                <th style={{ textAlign: 'center', padding: '14px 20px', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>超级权限</th>
                                <th style={{ textAlign: 'center', padding: '14px 20px', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {users.map(user => (
                                <tr key={user.id} style={{ borderBottom: '1px solid var(--border-color)', transition: 'background 0.2s' }} className="al-table-row">
                                    <td style={{ padding: '14px 20px', fontFamily: 'var(--font-mono, monospace)', fontSize: '0.85rem', textAlign: 'center' }}>
                                        {user.email}
                                    </td>
                                    <td style={{ padding: '14px 20px', textAlign: 'center' }}><RoleBadge role={user.role} /></td>
                                    <td style={{ padding: '14px 20px', textAlign: 'center' }}><StatusPill ok={user.is_active} label={user.is_active ? '正常' : '禁用'} /></td>
                                    <td style={{ padding: '14px 20px', textAlign: 'center' }}><StatusPill ok={user.is_verified} label={user.is_verified ? '已验证' : '待验证'} /></td>
                                    <td style={{ padding: '14px 20px', textAlign: 'center' }}><StatusPill ok={user.is_superuser} label={user.is_superuser ? '是' : '否'} /></td>
                                    <td style={{ padding: '14px 20px' }}>
                                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'center' }}>
                                            {!user.is_verified && (
                                                <button
                                                    className="al-btn-base"
                                                    style={{
                                                        padding: '4px 10px',
                                                        fontSize: '0.75rem',
                                                        background: 'var(--accent-glow)',
                                                        color: 'var(--accent-primary)',
                                                        border: '1px solid var(--accent-glow)'
                                                    }}
                                                    onClick={() => handleVerify(user.id)}
                                                    disabled={actionId === user.id}
                                                    title="验证该用户"
                                                >
                                                    {actionId === user.id
                                                        ? <Loader2 size={12} className="al-spin" />
                                                        : <><UserCheck size={12} /> <span>验证</span></>
                                                    }
                                                </button>
                                            )}

                                            <button
                                                className="al-btn-base"
                                                style={{
                                                    padding: '4px 10px', fontSize: '0.75rem',
                                                    background: user.is_active ? 'rgba(248, 113, 113, 0.1)' : 'rgba(74, 222, 128, 0.1)',
                                                    color: user.is_active ? '#f87171' : '#4ade80',
                                                    border: 'none'
                                                }}
                                                onClick={() => handleToggleStatus(user)}
                                                disabled={actionId === user.id}
                                                title={user.is_active ? '禁用用户' : '启用用户'}
                                            >
                                                {actionId === user.id
                                                    ? <Loader2 size={12} className="al-spin" />
                                                    : user.is_active
                                                        ? <><XCircle size={12} /> <span>禁用</span></>
                                                        : <><CheckCircle size={12} /> <span>启用</span></>
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

            {/* 创建用户模态框 */}
            {showCreateModal && (
                <div className="ce-overlay animate-fade-in" onClick={handleCloseCreateModal} style={{
                    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)',
                    zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center'
                }}>
                    <div className="ce-container glass-panel animate-scale-in"
                        style={{ width: '90%', maxWidth: '440px', background: 'var(--bg-secondary)', border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-xl)', overflow: 'hidden' }}
                        onClick={e => e.stopPropagation()}>

                        <div className="ce-header" style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <div style={{ background: 'var(--accent-glow)', color: 'var(--accent-primary)', padding: '6px', borderRadius: '8px' }}>
                                    <UserPlus size={18} />
                                </div>
                                <h3 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 600 }}>创建新用户</h3>
                            </div>
                            <button
                                className="al-btn-ghost"
                                onClick={handleCloseCreateModal}
                                style={{ border: 'none', background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer', padding: '4px' }}
                            >
                                <X size={20} />
                            </button>
                        </div>

                        <div className="ce-body" style={{ padding: '24px' }}>
                            {createdUser ? (
                                <div style={{ textAlign: 'center' }} className="animate-fade-in">
                                    <div style={{ width: '64px', height: '64px', background: 'rgba(74, 222, 128, 0.1)', color: '#4ade80', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
                                        <CheckCircle size={32} />
                                    </div>
                                    <h4 style={{ fontSize: '1.2rem', marginBottom: '8px' }}>创建成功</h4>
                                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginBottom: '24px' }}>
                                        已为 <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{createdUser.email}</span> 创建账户
                                    </p>

                                    <div style={{
                                        background: 'var(--bg-tertiary)',
                                        borderRadius: '12px',
                                        padding: '16px',
                                        marginBottom: '24px',
                                        border: '1px solid var(--border-color)',
                                        textAlign: 'left'
                                    }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}>
                                            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>初始密码</span>
                                            <span style={{ fontSize: '0.7rem', color: '#f87171', background: 'rgba(248, 113, 113, 0.1)', padding: '2px 6px', borderRadius: '4px' }}>请务必保存</span>
                                        </div>
                                        <div style={{
                                            background: 'var(--bg-input)',
                                            padding: '12px',
                                            borderRadius: '8px',
                                            fontFamily: 'var(--font-mono, monospace)',
                                            fontSize: '1.1rem',
                                            fontWeight: 600,
                                            color: 'var(--accent-primary)',
                                            textAlign: 'center',
                                            letterSpacing: '1px',
                                            border: '1px dashed var(--accent-glow)'
                                        }}>
                                            {createdUser.initial_password}
                                        </div>
                                    </div>

                                    <button
                                        className="al-btn-base"
                                        onClick={handleCopyPassword}
                                        style={{
                                            width: '100%',
                                            justifyContent: 'center',
                                            background: copied ? 'rgba(74, 222, 128, 0.1)' : 'var(--accent-primary)',
                                            color: copied ? '#4ade80' : 'white',
                                            border: copied ? '1px solid #4ade80' : 'none',
                                            height: '42px'
                                        }}
                                    >
                                        {copied ? <><Check size={16} /> <span>已复制到剪贴板</span></> : <><Copy size={16} /> <span>复制密码并继续</span></>}
                                    </button>
                                </div>
                            ) : (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                                    <div>
                                        <label style={{ display: 'block', marginBottom: '8px', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                                            邮箱地址 *
                                        </label>
                                        <input
                                            type="email"
                                            className="al-input-base"
                                            value={createEmail}
                                            onChange={e => setCreateEmail(e.target.value)}
                                            placeholder="请输入用户邮箱"
                                            disabled={createLoading}
                                            onKeyDown={e => e.key === 'Enter' && !createLoading && handleCreateUser()}
                                        />
                                    </div>

                                    <div>
                                        <label style={{ display: 'block', marginBottom: '8px', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                                            分配角色 *
                                        </label>
                                        <select
                                            className="al-input-base"
                                            value={createRole}
                                            onChange={e => setCreateRole(e.target.value as 'admin' | 'developer' | 'viewer')}
                                            disabled={createLoading}
                                            style={{ cursor: 'pointer', appearance: 'none' }}
                                        >
                                            <option value="developer">Developer - 开发者</option>
                                            <option value="viewer">Viewer - 查看者</option>
                                            <option value="admin">Admin - 管理员</option>
                                        </select>
                                    </div>

                                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', background: 'var(--bg-tertiary)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                                        <input
                                            type="checkbox"
                                            id="isVerified"
                                            checked={createVerified}
                                            onChange={e => setCreateVerified(e.target.checked)}
                                            disabled={createLoading}
                                            style={{ width: '18px', height: '18px', cursor: 'pointer', accentColor: 'var(--accent-primary)' }}
                                        />
                                        <label
                                            htmlFor="isVerified"
                                            style={{ fontSize: '0.85rem', cursor: 'pointer', userSelect: 'none', color: 'var(--text-primary)' }}
                                        >
                                            标记为已验证（跳过邮件确认）
                                        </label>
                                    </div>

                                    <button
                                        className="al-btn-base"
                                        onClick={handleCreateUser}
                                        disabled={createLoading || !createEmail.trim()}
                                        style={{
                                            marginTop: '8px',
                                            width: '100%',
                                            justifyContent: 'center',
                                            background: 'var(--accent-primary)',
                                            color: 'white',
                                            border: 'none',
                                            height: '42px'
                                        }}
                                    >
                                        {createLoading ? <><Loader2 size={16} className="al-spin" /> <span>创建中...</span></> : '立即创建用户'}
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
