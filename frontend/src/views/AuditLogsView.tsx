import { useState, useEffect, useCallback } from 'react';
import { ShieldCheck, Search, RefreshCw, Loader2, Download } from 'lucide-react';
import { fetchAuditLogs, exportAuditLogs } from '@/api/admin';
import type { AuditLogItem } from '@/api/admin';
import './AuditLogsView.css';

// ── 格式化时间 ──
function fmtTime(iso: string): string {
    const d = new Date(iso);
    const p = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// ── 操作动作颜色标签 ──
function ActionBadge({ action }: { action: string }) {
    const colorMap: Record<string, string> = {
        CREATE: '#4ade80', UPDATE: '#60a5fa', DELETE: '#f87171',
        LOGIN: '#a78bfa', LOGOUT: '#94a3b8', STATUS: '#fbbf24',
    };
    const color = colorMap[action.toUpperCase()] ?? '#64748b';
    return (
        <span className="al-badge" style={{ color, borderColor: color + '44', background: color + '14' }}>
            {action.toUpperCase()}
        </span>
    );
}

// ── 状态码颜色 ──
function StatusCode({ code }: { code: number }) {
    const color = code < 300 ? '#4ade80' : code < 400 ? '#fbbf24' : '#f87171';
    return <span style={{ color, fontWeight: 600, fontFamily: 'monospace' }}>{code}</span>;
}

const PAGE_SIZE = 50;

export default function AuditLogsView() {
    const [logs, setLogs] = useState<AuditLogItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [skip, setSkip] = useState(0);
    const [hasMore, setHasMore] = useState(true);

    // 筛选条件
    const [filterAction, setFilterAction] = useState('');
    const [filterResource, setFilterResource] = useState('');
    const [filterStartTime, setFilterStartTime] = useState('');
    const [filterEndTime, setFilterEndTime] = useState('');

    const loadLogs = useCallback(async (resetPage = false) => {
        setLoading(true);
        const page = resetPage ? 0 : skip;
        try {
            const res = await fetchAuditLogs({
                action: filterAction || undefined,
                resource_type: filterResource || undefined,
                start_time: filterStartTime || undefined,
                end_time: filterEndTime || undefined,
                skip: page,
                limit: PAGE_SIZE,
            });
            if (res?.data) {
                if (resetPage) {
                    setLogs(res.data);
                    setSkip(PAGE_SIZE);
                } else {
                    setLogs(prev => [...prev, ...res.data]);
                    setSkip(page + PAGE_SIZE);
                }
                setHasMore(res.data.length === PAGE_SIZE);
            }
        } catch {
            // silent
        } finally {
            setLoading(false);
        }
    }, [skip, filterAction, filterResource, filterStartTime, filterEndTime]);

    useEffect(() => {
        loadLogs(true);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const handleSearch = () => {
        setSkip(0);
        loadLogs(true);
    };

    const handleReset = () => {
        setFilterAction('');
        setFilterResource('');
        setFilterStartTime('');
        setFilterEndTime('');
        setSkip(0);
        setTimeout(() => loadLogs(true), 0);
    };

    const handleExport = async () => {
        setLoading(true);
        try {
            await exportAuditLogs({
                action: filterAction || undefined,
                resource_type: filterResource || undefined,
                start_time: filterStartTime || undefined,
                end_time: filterEndTime || undefined,
            });
        } catch (e) {
            console.error(e);
            alert('导出发生错误');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="al-container">
            {/* 标题栏 */}
            <div className="al-toolbar glass-panel">
                <div className="al-toolbar-left">
                    <ShieldCheck size={20} />
                    <h2>审计日志</h2>
                    <span className="al-readonly-badge">只读</span>
                </div>
                <div style={{ display: 'flex', gap: '10px' }}>
                    <button className="al-btn al-btn-ghost" onClick={handleExport} disabled={loading}>
                        <Download size={14} /> 导出 CSV
                    </button>
                    <button className="al-btn al-btn-ghost" onClick={() => loadLogs(true)} disabled={loading}>
                        <RefreshCw size={14} className={loading ? 'al-spin' : ''} />
                        刷新
                    </button>
                </div>
            </div>

            {/* 筛选栏 */}
            <div className="al-filter glass-panel">
                <div className="al-filter-row">
                    <div className="al-filter-item">
                        <label>操作动作</label>
                        <input
                            type="text"
                            value={filterAction}
                            onChange={e => setFilterAction(e.target.value)}
                            placeholder="CREATE / DELETE / LOGIN..."
                        />
                    </div>
                    <div className="al-filter-item">
                        <label>资源类型</label>
                        <input
                            type="text"
                            value={filterResource}
                            onChange={e => setFilterResource(e.target.value)}
                            placeholder="spider / project / user..."
                        />
                    </div>
                    <div className="al-filter-item">
                        <label>开始时间</label>
                        <input
                            type="datetime-local"
                            value={filterStartTime}
                            onChange={e => setFilterStartTime(e.target.value)}
                        />
                    </div>
                    <div className="al-filter-item">
                        <label>截止时间</label>
                        <input
                            type="datetime-local"
                            value={filterEndTime}
                            onChange={e => setFilterEndTime(e.target.value)}
                        />
                    </div>
                </div>
                <div className="al-filter-actions">
                    <button className="al-btn al-btn-primary" onClick={handleSearch} disabled={loading}>
                        <Search size={14} /> 查询
                    </button>
                    <button className="al-btn al-btn-ghost" onClick={handleReset} disabled={loading}>
                        重置
                    </button>
                </div>
            </div>

            {/* 表格 */}
            <div className="al-table-wrap glass-panel">
                {loading && logs.length === 0 ? (
                    <div className="al-empty">
                        <Loader2 size={28} className="al-spin" />
                        <p>加载中...</p>
                    </div>
                ) : logs.length === 0 ? (
                    <div className="al-empty">
                        <ShieldCheck size={44} strokeWidth={1} />
                        <p>暂无审计日志</p>
                    </div>
                ) : (
                    <>
                        <table className="al-table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>操作者</th>
                                    <th>角色</th>
                                    <th>动作</th>
                                    <th>资源类型</th>
                                    <th>资源 ID</th>
                                    <th>IP 地址</th>
                                    <th>状态码</th>
                                    <th>时间</th>
                                </tr>
                            </thead>
                            <tbody>
                                {logs.map(log => (
                                    <tr key={log.id}>
                                        <td className="al-id">{log.id}</td>
                                        <td className="al-mono al-truncate" title={log.operator_id}>
                                            {log.operator_id.slice(0, 8)}…
                                        </td>
                                        <td>
                                            <span className={`al-role al-role-${log.role}`}>{log.role}</span>
                                        </td>
                                        <td><ActionBadge action={log.action} /></td>
                                        <td className="al-resource-type">{log.resource_type}</td>
                                        <td className="al-mono al-truncate" title={log.resource_id}>
                                            {log.resource_id}
                                        </td>
                                        <td className="al-mono">{log.ip_address ?? '—'}</td>
                                        <td><StatusCode code={log.status_code} /></td>
                                        <td className="al-time">{fmtTime(log.created_at)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>

                        {/* 加载更多 */}
                        {hasMore && (
                            <div className="al-load-more">
                                <button
                                    className="al-btn al-btn-ghost"
                                    onClick={() => loadLogs(false)}
                                    disabled={loading}
                                >
                                    {loading
                                        ? <><Loader2 size={14} className="al-spin" /> 加载中...</>
                                        : '加载更多'
                                    }
                                </button>
                            </div>
                        )}
                    </>
                )}
            </div>

            {/* 只读提示条 */}
            <div className="al-readonly-notice">
                <ShieldCheck size={14} />
                审计日志为不可逆记录，本界面仅提供查询功能，不支持任何编辑或删除操作。
            </div>
        </div>
    );
}
