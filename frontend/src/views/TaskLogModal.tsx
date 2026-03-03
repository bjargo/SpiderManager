import { useState, useEffect, useRef, useCallback } from 'react';
import { X, Terminal, Loader2, FileText } from 'lucide-react';
import { fetchTaskLogs } from '@/api/spider';
import type { SpiderTaskItem, TaskLogItem, TaskStatus } from '@/types/spider';
import './TaskLogModal.css';

// ── 类型 ──
type ToastType = 'success' | 'error';

interface TaskLogModalProps {
    task: SpiderTaskItem;
    spiderId: number;
    onClose: () => void;
    showToast: (msg: string, type?: ToastType) => void;
}

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

export default function TaskLogModal({ task, spiderId, onClose, showToast }: TaskLogModalProps) {
    const [logs, setLogs] = useState<TaskLogItem[]>([]);
    const [wsLogs, setWsLogs] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const terminalRef = useRef<HTMLDivElement>(null);
    const wsRef = useRef<WebSocket | null>(null);

    const isLive = task.status === 'running' || task.status === 'pending';

    // ── 加载持久化日志 ──
    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            try {
                const res = await fetchTaskLogs(spiderId, task.task_id);
                if (!cancelled && res.code === 200 && res.data) {
                    setLogs(res.data);
                }
            } catch {
                if (!cancelled) showToast('加载日志失败', 'error');
            }
            if (!cancelled) setLoading(false);
        })();
        return () => { cancelled = true; };
    }, [spiderId, task.task_id]);

    // ── WebSocket 实时日志 ──
    useEffect(() => {
        if (!isLive) return;

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.hostname}:8000/api/tasks/ws/${task.task_id}`;
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onmessage = (event) => {
            setWsLogs(prev => [...prev, event.data]);
        };

        ws.onerror = () => {
            // WebSocket errors are silent - the user might not have a running worker
        };

        return () => {
            ws.close();
            wsRef.current = null;
        };
    }, [isLive, task.task_id]);

    // ── 自动滚动到底部 ──
    const scrollToBottom = useCallback(() => {
        if (terminalRef.current) {
            terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
        }
    }, []);

    useEffect(() => {
        scrollToBottom();
    }, [logs, wsLogs, scrollToBottom]);

    // ── 日志行分类 ──
    const classifyLine = (line: string): string => {
        if (line.startsWith('[SYSTEM:')) return 'system';
        if (line.toLowerCase().includes('error') || line.toLowerCase().includes('traceback')) return 'error';
        return '';
    };

    const allLogLines: string[] = [
        ...logs.map(l => l.content),
        ...wsLogs,
    ];

    return (
        <div className="tl-overlay" onClick={onClose}>
            <div className="tl-container" onClick={e => e.stopPropagation()}>
                {/* 头部 */}
                <div className="tl-header">
                    <div className="tl-header-left">
                        <h3><Terminal size={16} /> 任务日志</h3>
                        <span
                            className="tl-status-badge"
                            style={{
                                color: getStatusColor(task.status),
                                background: getStatusBg(task.status),
                            }}
                        >
                            {task.status.toUpperCase()}
                        </span>
                        {isLive && (
                            <span className="tl-live-indicator">
                                <span className="tl-live-dot" />
                                实时
                            </span>
                        )}
                    </div>
                    <button className="tl-close-btn" onClick={onClose}>
                        <X size={14} /> 关闭
                    </button>
                </div>

                {/* 终端日志区 */}
                {loading ? (
                    <div className="tl-terminal-loading">
                        <Loader2 size={20} className="spin" />
                        加载日志中...
                    </div>
                ) : allLogLines.length === 0 ? (
                    <div className="tl-terminal-empty">
                        <FileText size={40} strokeWidth={1} />
                        <p>暂无日志记录</p>
                    </div>
                ) : (
                    <div className="tl-terminal" ref={terminalRef}>
                        {allLogLines.map((line, i) => (
                            <div key={i} className={`tl-log-line ${classifyLine(line)}`}>
                                {line}
                            </div>
                        ))}
                    </div>
                )}

                {/* 底部状态栏 */}
                <div className="tl-status-bar">
                    <span>Task ID: {task.task_id}</span>
                    <span>{allLogLines.length} 行日志{isLive ? ' · WebSocket 已连接' : ''}</span>
                </div>
            </div>
        </div>
    );
}
