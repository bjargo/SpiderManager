import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Play, Bug, Clock, Server, Activity, StopCircle, RefreshCw, Terminal, CheckCircle, XCircle, Trash2, FileText } from 'lucide-react';
import { fetchTaskList, stopTask, deleteTask } from '@/api/task';
import { fetchSpiderList } from '@/api/spider';
import { fetchNodeList } from '@/api/node';
import type { SpiderTaskOut } from '@/types/task';
import type { SpiderItem } from '@/types/spider';
import type { SpiderNode } from '@/types/node';
import classNames from 'classnames';
import LogTerminal from '@/components/LogTerminal';
import './TaskManagement.css';

// ─────────────────────────────────────────────────
// 格式化时间
// ─────────────────────────────────────────────────
function formatDateTime(iso: string | null): string {
    if (!iso) return '-';
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function calculateDuration(start: string | null, end: string | null): string {
    if (!start) return '-';
    const s = new Date(start).getTime();
    const e = end ? new Date(end).getTime() : new Date().getTime();
    const diffSeconds = Math.floor((e - s) / 1000);

    if (diffSeconds < 60) return `${diffSeconds}s`;
    const m = Math.floor(diffSeconds / 60);
    const sRemain = diffSeconds % 60;
    if (m < 60) return `${m}m ${sRemain}s`;

    const h = Math.floor(m / 60);
    const mRemain = m % 60;
    return `${h}h ${mRemain}m ${sRemain}s`;
}

// ─────────────────────────────────────────────────
// 主视图
// ─────────────────────────────────────────────────
export default function TaskManagement() {
    const navigate = useNavigate();
    const location = useLocation();

    const [tasks, setTasks] = useState<SpiderTaskOut[]>([]);
    const [spiders, setSpiders] = useState<SpiderItem[]>([]);
    const [nodes, setNodes] = useState<SpiderNode[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);

    const [filterStatus, setFilterStatus] = useState<string>('');
    const [filterSpider, setFilterSpider] = useState<string>('');
    const [filterTaskId, setFilterTaskId] = useState<string>('');
    const [filterStartDate, setFilterStartDate] = useState<string>('');
    const [filterEndDate, setFilterEndDate] = useState<string>('');

    const [page, setPage] = useState(1);
    const limit = 20;

    const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
    const [stoppingId, setStoppingId] = useState<string | null>(null);
    const [isUrlParamsParsed, setIsUrlParamsParsed] = useState(false);

    // 解析 URL 参数
    useEffect(() => {
        const query = new URLSearchParams(location.search);

        // 任务ID筛选
        const navTaskId = query.get('taskId');
        if (navTaskId) {
            setFilterTaskId(navTaskId);
        }

        // 日期筛选
        const dateParam = query.get('date');
        if (dateParam === 'today') {
            const today = new Date().toISOString().slice(0, 10);
            setFilterStartDate(today);
            setFilterEndDate(today);
        } else if (dateParam === 'week') {
            const now = new Date();
            const day = now.getDay() === 0 ? 6 : now.getDay() - 1;
            const mon = new Date(now);
            mon.setDate(now.getDate() - day);
            setFilterStartDate(mon.toISOString().slice(0, 10));
            setFilterEndDate(now.toISOString().slice(0, 10));
        } else if (dateParam === 'month') {
            const now = new Date();
            const firstDay = new Date(now.getFullYear(), now.getMonth(), 1);
            setFilterStartDate(firstDay.toISOString().slice(0, 10));
            setFilterEndDate(now.toISOString().slice(0, 10));
        }

        // 状态筛选
        const statusParam = query.get('status');
        if (statusParam) {
            setFilterStatus(statusParam);
        }

        // 标记 URL 参数已解析完成
        setIsUrlParamsParsed(true);
    }, [location.search]);

    const loadTasks = useCallback(async () => {
        setLoading(true);
        try {
            const params: any = { skip: (page - 1) * limit, limit };
            if (filterStatus) params.status = filterStatus;
            if (filterSpider) params.spider_id = Number(filterSpider);
            if (filterTaskId) params.task_id = filterTaskId;
            if (filterStartDate) params.start_time = `${filterStartDate} 00:00:00`;
            if (filterEndDate) params.end_time = `${filterEndDate} 23:59:59`;

            const res = await fetchTaskList(params);
            if (res.code === 200 && res.data) {
                setTasks(res.data.items);
                setTotal(res.data.total);
            }
        } catch { /* silent */ }
        setLoading(false);
    }, [page, limit, filterStatus, filterSpider, filterTaskId, filterStartDate, filterEndDate]);

    const loadSpiders = useCallback(async () => {
        try {
            const res = await fetchSpiderList();
            if (res.code === 200 && res.data) setSpiders(res.data);
        } catch { /* silent */ }
    }, []);

    const loadNodes = useCallback(async () => {
        try {
            const res = await fetchNodeList();
            if (res.code === 200 && res.data) setNodes(res.data);
        } catch { /* silent */ }
    }, []);

    useEffect(() => {
        loadSpiders();
        loadNodes();
    }, [loadSpiders, loadNodes]);

    useEffect(() => {
        // 只有在 URL 参数解析完成后才加载数据
        if (isUrlParamsParsed) {
            loadTasks();
        }
    }, [loadTasks, isUrlParamsParsed]);

    useEffect(() => {
        // 自动刷新机制: 如果有任务处于 running/pending 状态，则每5秒刷新一次
        const hasActiveTasks = tasks.some(t => t.status === 'running' || t.status === 'pending');
        if (hasActiveTasks) {
            const timer = setInterval(loadTasks, 5000);
            return () => clearInterval(timer);
        }
    }, [loadTasks, tasks]);

    const handleStopTask = async (e: React.MouseEvent, taskId: string) => {
        e.stopPropagation();
        setStoppingId(taskId);
        try {
            const res = await stopTask(taskId);
            if (res.code === 200) {
                loadTasks();
            }
        } catch (err) {
            console.error(err);
        } finally {
            setStoppingId(null);
        }
    };

    const handleDeleteTask = async (e: React.MouseEvent, taskId: string) => {
        e.stopPropagation();
        if (!window.confirm('确定要删除这条任务记录吗？相关日志也将一并删除。')) return;
        try {
            const res = await deleteTask(taskId);
            if (res.code === 200) {
                if (activeTaskId === taskId) setActiveTaskId(null);
                loadTasks();
            }
        } catch (err) {
            console.error(err);
        }
    };

    const getStatusBadge = (status: string) => {
        switch (status) {
            case 'success':
                return <span className="tm-badge success"><CheckCircle size={12} /> Success</span>;
            case 'error':
            case 'failed':
            case 'timeout':
                return <span className="tm-badge error"><XCircle size={12} /> {status.charAt(0).toUpperCase() + status.slice(1)}</span>;
            case 'cancelled':
                return <span className="tm-badge error"><StopCircle size={12} /> Cancelled</span>;
            case 'running':
                return <span className="tm-badge running"><Activity size={12} className="spin" /> Running</span>;
            case 'pending':
                return <span className="tm-badge pending"><Clock size={12} /> Pending</span>;
            default:
                return <span className="tm-badge pending">{status}</span>;
        }
    };

    const handleClearDates = () => {
        setFilterStartDate('');
        setFilterEndDate('');
        setPage(1);
    };

    // ── 快捷日期 ──
    const toDateStr = (d: Date) => d.toISOString().slice(0, 10);

    const handleShortcutToday = () => {
        const today = toDateStr(new Date());
        setFilterStartDate(today);
        setFilterEndDate(today);
        setPage(1);
    };

    const handleShortcutWeek = () => {
        const now = new Date();
        const day = now.getDay() === 0 ? 6 : now.getDay() - 1; // Mon=0
        const mon = new Date(now);
        mon.setDate(now.getDate() - day);
        setFilterStartDate(toDateStr(mon));
        setFilterEndDate(toDateStr(now));
        setPage(1);
    };

    const handleShortcutMonth = () => {
        const now = new Date();
        const firstDay = new Date(now.getFullYear(), now.getMonth(), 1);
        setFilterStartDate(toDateStr(firstDay));
        setFilterEndDate(toDateStr(now));
        setPage(1);
    };

    return (
        <div className="tm-container">
            <div className="tm-toolbar glass-panel">
                <div className="tm-toolbar-left">
                    <h2><Play size={20} /> 任务追踪</h2>
                    <span className="tm-count">共 {total} 条记录</span>
                </div>
                <div className="tm-filters">
                    {/* 状态筛选 */}
                    <div className="tm-filter-item">
                        <select
                            value={filterStatus}
                            onChange={(e) => { setFilterStatus(e.target.value); setPage(1); }}
                        >
                            <option value="">所有状态</option>
                            <option value="pending">Pending</option>
                            <option value="running">Running</option>
                            <option value="success">Success</option>
                            <option value="error">Error</option>
                            <option value="cancelled">Cancelled</option>
                        </select>
                    </div>

                    {/* 爬虫筛选 */}
                    <div className="tm-filter-item">
                        <select
                            value={filterSpider}
                            onChange={(e) => { setFilterSpider(e.target.value); setPage(1); }}
                        >
                            <option value="">所有爬虫</option>
                            {spiders.map(s => (
                                <option key={s.id} value={s.id}>{s.name}</option>
                            ))}
                        </select>
                    </div>

                    {/* 日期筛选区 */}
                    <div className="tm-date-range">
                        <div className="tm-date-shortcuts">
                            <button className="tm-shortcut-btn" onClick={handleShortcutToday}>今天</button>
                            <button className="tm-shortcut-btn" onClick={handleShortcutWeek}>本周</button>
                            <button className="tm-shortcut-btn" onClick={handleShortcutMonth}>本月</button>
                        </div>
                        <div className="tm-date-inputs">
                            <input
                                type="date"
                                value={filterStartDate}
                                onChange={(e) => { setFilterStartDate(e.target.value); setPage(1); }}
                                className="tm-date-picker"
                                title="开始日期"
                            />
                            <span className="tm-date-sep">→</span>
                            <input
                                type="date"
                                value={filterEndDate}
                                onChange={(e) => { setFilterEndDate(e.target.value); setPage(1); }}
                                className="tm-date-picker"
                                title="结束日期"
                            />
                            {(filterStartDate || filterEndDate) && (
                                <button className="tm-btn-clear" onClick={handleClearDates} title="清除日期">
                                    <XCircle size={14} />
                                </button>
                            )}
                        </div>
                    </div>

                    <button className="tm-btn-refresh" onClick={loadTasks} disabled={loading} title="刷新列表">
                        <RefreshCw size={16} className={classNames({ spin: loading })} />
                    </button>
                </div>
            </div>

            <div className="tm-content">
                <div className="tm-table-wrap glass-panel">
                    <table className="tm-table">
                        <thead>
                            <tr>
                                <th>任务 ID</th>
                                <th>所属爬虫</th>
                                <th>状态</th>
                                <th>执行节点</th>
                                <th>开始时间</th>
                                <th>运行时长</th>
                                <th style={{ width: 220 }}>操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {tasks.length === 0 ? (
                                <tr>
                                    <td colSpan={7} className="tm-empty">暂无任务记录</td>
                                </tr>
                            ) : (
                                tasks.map(task => (
                                    <tr
                                        key={task.id}
                                        className={classNames({ 'active-row': activeTaskId === task.task_id })}
                                    >
                                        <td className="tm-id">
                                            <div className="tm-id-wrapper">
                                                <Terminal size={14} /> {task.task_id}
                                            </div>
                                        </td>
                                        <td className="tm-spider"
                                            onClick={(e) => { e.stopPropagation(); navigate(`/spiders?id=${task.spider_id}`); }}
                                            style={{ cursor: 'pointer', color: 'var(--accent-primary)' }}
                                            title="跳转到该爬虫"
                                        >
                                            <div className="tm-spider-wrapper">
                                                <Bug size={14} /> {task.spider_name}
                                            </div>
                                        </td>
                                        <td>{getStatusBadge(task.status)}</td>
                                        <td className="tm-node">
                                            {task.node_id ? (
                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                    <Server size={14} />
                                                    <span>{nodes.find(n => n.node_id === task.node_id)?.name || task.node_id}</span>
                                                    {(() => {
                                                        const node = nodes.find(n => n.node_id === task.node_id);
                                                        if (!node) return null;
                                                        return (
                                                            <span
                                                                title={`节点状态: ${node.status}`}
                                                                style={{
                                                                    display: 'inline-block',
                                                                    width: '8px',
                                                                    height: '8px',
                                                                    borderRadius: '50%',
                                                                    backgroundColor: node.status === 'online' ? '#4ade80' : '#f87171',
                                                                    flexShrink: 0
                                                                }}
                                                            />
                                                        );
                                                    })()}
                                                </div>
                                            ) : '-'}
                                        </td>
                                        <td className="tm-time">{formatDateTime(task.started_at || task.created_at)}</td>
                                        <td className="tm-duration">{calculateDuration(task.started_at, task.finished_at)}</td>
                                        <td>
                                            <div className="tm-actions">
                                                <button
                                                    className="tm-action-btn log"
                                                    onClick={(e) => { e.stopPropagation(); setActiveTaskId(task.task_id); }}
                                                    title="查看日志"
                                                >
                                                    <FileText size={14} /> 日志
                                                </button>
                                                {(task.status === 'running' || task.status === 'pending') && (
                                                    <button
                                                        className="tm-action-btn stop"
                                                        disabled={stoppingId === task.task_id}
                                                        onClick={(e) => handleStopTask(e, task.task_id)}
                                                        title="强制终止"
                                                    >
                                                        <StopCircle size={16} /> 终止
                                                    </button>
                                                )}
                                                {task.status !== 'running' && (
                                                    <button
                                                        className="tm-action-btn delete"
                                                        onClick={(e) => handleDeleteTask(e, task.task_id)}
                                                        title="删除记录"
                                                    >
                                                        <Trash2 size={14} /> 删除
                                                    </button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>

                    {total > limit && (
                        <div className="tm-pagination">
                            <button
                                disabled={page === 1}
                                onClick={() => setPage(page - 1)}
                            >上一页</button>
                            <span>第 {page} 页 / 共 {Math.ceil(total / limit)} 页</span>
                            <button
                                disabled={page >= Math.ceil(total / limit)}
                                onClick={() => setPage(page + 1)}
                            >下一页</button>
                        </div>
                    )}
                </div>

                {/* 侧边日志抽屉 */}
                {activeTaskId && (
                    <>
                        <div className="tm-log-drawer-overlay" onClick={() => setActiveTaskId(null)} />
                        <div className="tm-log-drawer animate-fade-in-right">
                            <LogTerminal
                                taskId={activeTaskId}
                                taskStatus={tasks.find(t => t.task_id === activeTaskId)?.status}
                                onStop={async (tid) => {
                                    await stopTask(tid);
                                    loadTasks();
                                }}
                                onClose={() => setActiveTaskId(null)}
                            />
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
