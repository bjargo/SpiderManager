import React, { useState, useEffect, useCallback } from 'react';
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

    const [tasks, setTasks] = useState<SpiderTaskOut[]>([]);
    const [spiders, setSpiders] = useState<SpiderItem[]>([]);
    const [nodes, setNodes] = useState<SpiderNode[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);

    const [filterStatus, setFilterStatus] = useState<string>('');
    const [filterSpider, setFilterSpider] = useState<string>('');

    const [page, setPage] = useState(1);
    const limit = 20;

    const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
    const [stoppingId, setStoppingId] = useState<string | null>(null);

    const loadTasks = useCallback(async () => {
        setLoading(true);
        try {
            const params: any = { skip: (page - 1) * limit, limit };
            if (filterStatus) params.status = filterStatus;
            if (filterSpider) params.spider_id = Number(filterSpider);

            const res = await fetchTaskList(params);
            if (res.code === 200 && res.data) {
                setTasks(res.data.items);
                setTotal(res.data.total);
            }
        } catch { /* silent */ }
        setLoading(false);
    }, [page, limit, filterStatus, filterSpider]);

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
        loadTasks();
    }, [loadTasks]);

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

    return (
        <div className="tm-container">
            <div className="tm-toolbar glass-panel">
                <div className="tm-toolbar-left">
                    <h2><Play size={20} /> 任务追踪</h2>
                    <span className="tm-count">共 {total} 条记录</span>
                </div>
                <div className="tm-filters">
                    <div className="tm-filter-item">
                        <select
                            value={filterStatus}
                            onChange={(e) => { setFilterStatus(e.target.value); setPage(1); }}
                        >
                            <option value="">所有状态</option>
                            <option value="pending">Pending (等待中)</option>
                            <option value="running">Running (运行中)</option>
                            <option value="success">Success (成功)</option>
                            <option value="error">Error (失败)</option>
                            <option value="cancelled">Cancelled (已终止)</option>
                        </select>
                    </div>
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
                                            <Terminal size={14} /> {task.task_id}
                                        </td>
                                        <td className="tm-spider">
                                            <Bug size={14} /> {task.spider_name}
                                        </td>
                                        <td>{getStatusBadge(task.status)}</td>
                                        <td className="tm-node">
                                            {task.node_id ? <><Server size={14} /> {nodes.find(n => n.node_id === task.node_id)?.name || task.node_id}</> : '-'}
                                        </td>
                                        <td className="tm-time">{formatDateTime(task.started_at || task.created_at)}</td>
                                        <td className="tm-duration">{calculateDuration(task.started_at, task.finished_at)}</td>
                                        <td className="tm-actions">
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
