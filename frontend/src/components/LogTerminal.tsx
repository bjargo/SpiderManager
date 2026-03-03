import React, { useEffect, useRef, useState } from 'react';
import { useLogSocket } from '@/hooks/useLogSocket';
import { fetchTaskLogs } from '@/api/task';
import { Terminal, XCircle, RefreshCw, X, StopCircle, History } from 'lucide-react';
import classNames from 'classnames';
import './LogTerminal.css';

interface Props {
    taskId: string | null;
    taskStatus?: string;
    onStop?: (taskId: string) => void;
    onClose?: () => void;
}

const LogTerminal: React.FC<Props> = ({ taskId, taskStatus, onStop, onClose }) => {
    const isRunning = taskStatus === 'running';

    // --- WebSocket 实时日志 (仅 running 时启用) ---
    const { logs: wsLogs, status: wsStatus, setLogs: setWsLogs } = useLogSocket(isRunning ? taskId : null);

    // --- HTTP 历史日志 (非 running 时使用) ---
    const [historyLogs, setHistoryLogs] = useState<{ id: string; text: string }[]>([]);
    const [historyLoading, setHistoryLoading] = useState(false);

    const scrollRef = useRef<HTMLDivElement>(null);
    const [autoScroll, setAutoScroll] = useState(true);

    // 加载历史日志
    useEffect(() => {
        if (!taskId || isRunning) {
            setHistoryLogs([]);
            return;
        }

        const loadHistory = async () => {
            setHistoryLoading(true);
            try {
                const res = await fetchTaskLogs(taskId);
                if (res.code === 200 && res.data) {
                    setHistoryLogs(
                        res.data.map((item: any) => ({
                            id: String(item.id),
                            text: item.content,
                        }))
                    );
                }
            } catch (e) {
                console.error('Failed to load task logs:', e);
            }
            setHistoryLoading(false);
        };

        loadHistory();
    }, [taskId, isRunning]);

    // 当前显示的日志
    const logs = isRunning ? wsLogs : historyLogs;

    // Auto scroll logic
    useEffect(() => {
        if (autoScroll && scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [logs, autoScroll]);

    const handleScroll = () => {
        if (!scrollRef.current) return;
        const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
        if (scrollHeight - scrollTop - clientHeight > 30) {
            setAutoScroll(false);
        } else {
            setAutoScroll(true);
        }
    };

    if (!taskId) return null;

    const renderStatusText = () => {
        if (!isRunning) {
            if (historyLoading) return 'Loading history...';
            return `History (${historyLogs.length} lines)`;
        }
        switch (wsStatus) {
            case 'connecting': return 'Reconnecting...';
            case 'connected': return 'Connected (Streaming)';
            case 'error': return 'Connection Error';
            case 'closed': return 'Disconnected';
            default: return wsStatus;
        }
    };

    const statusClass = isRunning ? wsStatus : 'history';

    return (
        <div className="log-terminal-wrapper glass-panel animate-fade-in">
            <div className="term-header">
                <div className="term-info">
                    {isRunning ? <Terminal size={16} /> : <History size={16} />}
                    <span>{isRunning ? 'Real-time Task Logs' : 'Task Log History'}</span>
                    <div className={classNames('term-status', statusClass)}>
                        {isRunning && <div className="status-dot-blink" />}
                        <span style={{ fontSize: '10px', fontWeight: 600 }}>{renderStatusText()}</span>
                    </div>
                </div>
                <div className="term-actions">
                    {isRunning && (
                        <button
                            className="term-btn"
                            title={autoScroll ? 'Disable Auto-scroll' : 'Enable Auto-scroll'}
                            onClick={() => setAutoScroll(!autoScroll)}
                            style={{ color: autoScroll ? 'var(--accent-primary)' : 'inherit' }}
                        >
                            <RefreshCw size={14} className={classNames({ spin: wsStatus === 'connecting' })} />
                        </button>
                    )}
                    {isRunning && (
                        <button className="term-btn" title="Clear Logs" onClick={() => setWsLogs([])}>
                            <XCircle size={14} />
                        </button>
                    )}
                    {isRunning && onStop && (
                        <button
                            className="term-btn stop-btn-red"
                            title="Force Stop Task"
                            onClick={() => onStop(taskId)}
                        >
                            <StopCircle size={16} />
                            <span style={{ fontSize: '12px', fontWeight: 600 }}>STOP</span>
                        </button>
                    )}
                    {onClose && (
                        <button className="term-btn close" title="Close Terminal" onClick={onClose}>
                            <X size={16} />
                        </button>
                    )}
                </div>
            </div>

            <div className="term-body" ref={scrollRef} onScroll={handleScroll}>
                {logs.length === 0 ? (
                    <div className="term-empty-state">
                        {isRunning
                            ? (wsStatus === 'connecting' ? '> Initializing connection...' : '> Waiting for task activity...')
                            : (historyLoading ? '> Loading log history...' : '> No log records found.')
                        }
                    </div>
                ) : (
                    <div className="term-content">
                        {logs.map(log => (
                            <div key={log.id} className="log-line">
                                <span style={{ color: '#555', marginRight: '8px' }}>$</span>
                                {log.text}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

export default LogTerminal;
