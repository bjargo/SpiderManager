import { useState, useEffect } from 'react';
import { X, History, Loader2, FileText, Terminal, Clock } from 'lucide-react';
import { fetchSpiderTasks } from '@/api/spider';
import type { SpiderItem, SpiderTaskItem, TaskStatus } from '@/types/spider';
import TaskLogModal from './TaskLogModal';
import './TaskHistoryModal.css';

// ── 类型 ──
type ToastType = 'success' | 'error';

interface TaskHistoryModalProps {
    spider: SpiderItem;
    onClose: () => void;
    showToast: (msg: string, type?: ToastType) => void;
}

// ── 工具函数 ──
function getStatusColor(status: TaskStatus): string {
    const map: Record<string, string> = {
        running: '#3b82f6', pending: '#f59e0b', success: '#4ade80',
        failed: '#f87171', timeout: '#fb923c', error: '#ef4444', idle: '#6b7280',
    };
    return map[status] ?? '#6b7280';
}

function getStatusBg(status: TaskStatus): string {
    const map: Record<string, string> = {
        running: 'rgba(59,130,246,0.15)', pending: 'rgba(245,158,11,0.15)',
        success: 'rgba(74,222,128,0.15)', failed: 'rgba(248,113,113,0.15)',
        timeout: 'rgba(251,146,60,0.15)', error: 'rgba(239,68,68,0.15)',
        idle: 'rgba(107,114,128,0.15)',
    };
    return map[status] ?? 'rgba(107,114,128,0.15)';
}

function getStatusLabel(status: TaskStatus): string {
    const map: Record<string, string> = {
        running: '运行中', pending: '等待中', success: '成功',
        failed: '失败', timeout: '超时', error: '异常', idle: '空闲',
    };
    return map[status] ?? status;
}

function formatDateTime(iso: string): string {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function computeDuration(startedAt: string | null, finishedAt: string | null): string {
    if (!startedAt) return '-';
    const start = new Date(startedAt).getTime();
    const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
    const seconds = Math.floor((end - start) / 1000);
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

// ── 主组件 ──
export default function TaskHistoryModal({ spider, onClose, showToast }: TaskHistoryModalProps) {
    const [tasks, setTasks] = useState<SpiderTaskItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [logTarget, setLogTarget] = useState<SpiderTaskItem | null>(null);

    // ── 加载任务列表 ──
    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            try {
                const res = await fetchSpiderTasks(spider.id);
                if (!cancelled && res.code === 200 && res.data) {
                    setTasks(res.data);
                }
            } catch {
                if (!cancelled) showToast('加载任务历史失败', 'error');
            }
            if (!cancelled) setLoading(false);
        })();
        return () => { cancelled = true; };
    }, [spider.id]);

    return (
        <>
            <div className="th-overlay" onClick={onClose}>
                <div className="th-container" onClick={e => e.stopPropagation()}>
                    {/* 头部 */}
                    <div className="th-header">
                        <div className="th-header-left">
                            <h3><History size={16} /> {spider.name} — 任务历史</h3>
                        </div>
                        <button className="th-close-btn" onClick={onClose}>
                            <X size={14} /> 关闭
                        </button>
                    </div>

                    {/* 表格 */}
                    <div className="th-body">
                        {loading ? (
                            <div className="th-loading">
                                <Loader2 size={24} className="th-spin" />
                                加载中...
                            </div>
                        ) : tasks.length === 0 ? (
                            <div className="th-empty">
                                <Clock size={48} strokeWidth={1} />
                                <p>暂无任务历史记录</p>
                            </div>
                        ) : (
                            <table className="th-table">
                                <thead>
                                    <tr>
                                        <th>任务 ID</th>
                                        <th>状态</th>
                                        <th>节点</th>
                                        <th>命令</th>
                                        <th>创建时间</th>
                                        <th>耗时</th>
                                        <th style={{ width: 100 }}>操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {tasks.map(t => (
                                        <tr key={t.id}>
                                            <td>
                                                <span className="th-task-id" title={t.task_id}>
                                                    {t.task_id}
                                                </span>
                                            </td>
                                            <td>
                                                <span
                                                    className="th-status-tag"
                                                    style={{
                                                        color: getStatusColor(t.status),
                                                        background: getStatusBg(t.status),
                                                    }}
                                                >
                                                    {t.status === 'running' && <span className="th-running-dot" />}
                                                    {getStatusLabel(t.status)}
                                                </span>
                                            </td>
                                            <td>
                                                <span className="th-task-id">
                                                    {t.node_id || '-'}
                                                </span>
                                            </td>
                                            <td>
                                                <span className="th-task-id">
                                                    {t.command || '-'}
                                                </span>
                                            </td>
                                            <td>
                                                <span className="th-time">{formatDateTime(t.created_at)}</span>
                                            </td>
                                            <td>
                                                <span className="th-duration">
                                                    {computeDuration(t.started_at, t.finished_at)}
                                                </span>
                                            </td>
                                            <td>
                                                <button
                                                    className={`th-log-btn ${(t.status === 'running' || t.status === 'pending') ? 'live' : ''}`}
                                                    onClick={() => setLogTarget(t)}
                                                >
                                                    {(t.status === 'running' || t.status === 'pending')
                                                        ? <><Terminal size={12} /> 实时日志</>
                                                        : <><FileText size={12} /> 查看日志</>
                                                    }
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>

                    {/* 底部 */}
                    <div className="th-footer">
                        <span>共 {tasks.length} 条任务记录</span>
                    </div>
                </div>
            </div>

            {/* 日志弹窗 */}
            {logTarget && (
                <TaskLogModal
                    task={logTarget}
                    spiderId={spider.id}
                    onClose={() => setLogTarget(null)}
                    showToast={showToast}
                />
            )}
        </>
    );
}
