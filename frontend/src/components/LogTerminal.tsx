import React, { useEffect, useRef, useState } from 'react';
import { useLogSocket } from '@/hooks/useLogSocket';
import { useDataSocket, DataItem } from '@/hooks/useDataSocket';
import { fetchTaskLogs, fetchTaskData } from '@/api/task';
import { Terminal, XCircle, RefreshCw, X, StopCircle, Database, ChevronLeft, ChevronRight, FileCode } from 'lucide-react';
import classNames from 'classnames';
import './LogTerminal.css';

interface Props {
    taskId: string | null;
    taskStatus?: string;
    onStop?: (taskId: string) => void;
    onClose?: () => void;
    defaultTab?: TabType;
}

type TabType = 'logs' | 'data';

const LogTerminal: React.FC<Props> = ({ taskId, taskStatus, onStop, onClose, defaultTab = 'logs' }) => {
    const isLive = taskStatus === 'running' || taskStatus === 'pending';
    const [activeTab, setActiveTab] = useState<TabType>(defaultTab);

    useEffect(() => {
        if (taskId) {
            setActiveTab(defaultTab);
        }
    }, [taskId, defaultTab]);

    // ── 日志相关状态 ──
    const loadLogHistory = async (tid: string) => {
        setLogHistoryLoading(true);
        try {
            const res = await fetchTaskLogs(tid);
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
        setLogHistoryLoading(false);
    };

    const { logs: wsLogs, status: wsStatus, setLogs: setWsLogs } = useLogSocket(
        isLive ? taskId : null,
        { onStreamEnd: () => taskId && loadLogHistory(taskId) }
    );

    const [historyLogs, setHistoryLogs] = useState<{ id: string; text: string }[]>([]);
    const [logHistoryLoading, setLogHistoryLoading] = useState(false);

    // ── 数据相关状态 ──
    const { data: wsData, status: dataWsStatus, totalCount: wsTotalCount, clearData } = useDataSocket(
        isLive ? taskId : null
    );

    const [historyData, setHistoryData] = useState<DataItem[]>([]);
    const [historyTotal, setHistoryTotal] = useState(0);
    const [historyLoading, setHistoryLoading] = useState(false);
    const [historyPage, setHistoryPage] = useState(0);
    const historyPageSize = 50;
    const [selectedDataItem, setSelectedDataItem] = useState<DataItem | null>(null);

    const scrollRef = useRef<HTMLDivElement>(null);
    const [autoScroll, setAutoScroll] = useState(true);

    // 加载历史数据
    const loadDataHistory = async (tid: string, page: number = 0) => {
        setHistoryLoading(true);
        try {
            const res = await fetchTaskData(tid, page * historyPageSize, historyPageSize);
            if (res.code === 200 && res.data) {
                const items = res.data.items.map((item: any) => ({
                    id: String(item.id),
                    data: item.data,
                    timestamp: item.created_at,
                }));
                setHistoryData(items);
                setHistoryTotal(res.data.total);
                setHistoryPage(page);
            }
        } catch (e) {
            console.error('Failed to load task data:', e);
        }
        setHistoryLoading(false);
    };

    // 非 live 状态时同时加载日志和数据历史，确保两个 tab 的计数都能正确显示
    useEffect(() => {
        if (!taskId || isLive) {
            setHistoryLogs([]);
            setHistoryData([]);
            setHistoryTotal(0);
            setHistoryPage(0);
            return;
        }
        loadLogHistory(taskId);
        loadDataHistory(taskId, 0);
    }, [taskId, isLive]);

    // 当前显示的日志
    const logs = (isLive && wsStatus !== 'stream_ended') ? wsLogs : historyLogs;

    // 当前显示的数据
    const displayData = isLive ? wsData : historyData;

    // Auto scroll logic
    useEffect(() => {
        if (autoScroll && scrollRef.current && activeTab === 'logs') {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [logs, autoScroll, activeTab]);

    const handleScroll = () => {
        if (!scrollRef.current) return;
        const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
        if (scrollHeight - scrollTop - clientHeight > 30) {
            setAutoScroll(false);
        } else {
            setAutoScroll(true);
        }
    };

    // 格式化 JSON 显示
    const formatJson = (data: Record<string, any>): string => {
        return JSON.stringify(data, null, 2);
    };

    // 获取数据字段预览
    const getDataPreview = (data: Record<string, any>): string => {
        const keys = Object.keys(data);
        if (keys.length === 0) return '{}';
        const preview = keys.slice(0, 3).map(k => `${k}: ${String(data[k]).substring(0, 20)}`).join(', ');
        return keys.length > 3 ? `${preview}...` : preview;
    };

    if (!taskId) return null;

    const renderLogStatusText = () => {
        if (wsStatus === 'stream_ended') return `History (${historyLogs.length} lines)`;
        if (!isLive) {
            if (logHistoryLoading) return 'Loading...';
            return `History (${historyLogs.length} lines)`;
        }
        switch (wsStatus) {
            case 'connecting': return 'Reconnecting...';
            case 'connected': return 'Streaming';
            case 'error': return 'Error';
            case 'closed': return 'Disconnected';
            default: return wsStatus;
        }
    };

    const renderDataStatusText = () => {
        if (!isLive) {
            if (historyLoading) return 'Loading...';
            return `History (${historyTotal} records)`;
        }
        switch (dataWsStatus) {
            case 'connecting': return 'Reconnecting...';
            case 'connected': return `Streaming (${wsTotalCount})`;
            case 'error': return 'Error';
            case 'closed': return 'Disconnected';
            default: return dataWsStatus;
        }
    };

    const logStatusClass = (isLive && wsStatus !== 'stream_ended') ? wsStatus : 'history';
    const dataStatusClass = isLive ? dataWsStatus : 'history';

    // 分页控制
    const totalPages = Math.ceil(historyTotal / historyPageSize);
    const canPrev = historyPage > 0;
    const canNext = historyPage < totalPages - 1;

    return (
        <div className="log-terminal-wrapper glass-panel animate-fade-in">
            <div className="term-header">
                <div className="term-tabs">
                    <button
                        className={classNames('term-tab', { active: activeTab === 'logs' })}
                        onClick={() => setActiveTab('logs')}
                    >
                        <Terminal size={14} />
                        <span>Logs</span>
                        <div className={classNames('term-tab-status', logStatusClass)}>
                            {isLive && wsStatus === 'connected' && <div className="status-dot-blink" />}
                            <span>{renderLogStatusText()}</span>
                        </div>
                    </button>
                    <button
                        className={classNames('term-tab', { active: activeTab === 'data' })}
                        onClick={() => setActiveTab('data')}
                    >
                        <Database size={14} />
                        <span>Data</span>
                        <div className={classNames('term-tab-status', dataStatusClass)}>
                            {isLive && dataWsStatus === 'connected' && <div className="status-dot-blink" />}
                            <span>{renderDataStatusText()}</span>
                        </div>
                    </button>
                </div>
                <div className="term-actions">
                    {activeTab === 'logs' && (isLive && wsStatus !== 'stream_ended') && (
                        <>
                            <button
                                className="term-btn"
                                title={autoScroll ? 'Disable Auto-scroll' : 'Enable Auto-scroll'}
                                onClick={() => setAutoScroll(!autoScroll)}
                                style={{ color: autoScroll ? 'var(--accent-primary)' : 'inherit' }}
                            >
                                <RefreshCw size={14} className={classNames({ spin: wsStatus === 'connecting' })} />
                            </button>
                            <button className="term-btn" title="Clear Logs" onClick={() => setWsLogs([])}>
                                <XCircle size={14} />
                            </button>
                        </>
                    )}
                    {activeTab === 'data' && isLive && (
                        <>
                            <button
                                className="term-btn"
                                title={autoScroll ? 'Disable Auto-scroll' : 'Enable Auto-scroll'}
                                onClick={() => setAutoScroll(!autoScroll)}
                                style={{ color: autoScroll ? 'var(--accent-primary)' : 'inherit' }}
                            >
                                <RefreshCw size={14} className={classNames({ spin: dataWsStatus === 'connecting' })} />
                            </button>
                            <button className="term-btn" title="Clear Data" onClick={clearData}>
                                <XCircle size={14} />
                            </button>
                        </>
                    )}
                    {activeTab === 'data' && !isLive && historyTotal > 0 && (
                        <div className="term-pagination">
                            <button
                                className="term-page-btn"
                                disabled={!canPrev || historyLoading}
                                onClick={() => loadDataHistory(taskId, historyPage - 1)}
                            >
                                <ChevronLeft size={14} />
                            </button>
                            <span className="term-page-info">
                                {historyPage + 1} / {totalPages}
                            </span>
                            <button
                                className="term-page-btn"
                                disabled={!canNext || historyLoading}
                                onClick={() => loadDataHistory(taskId, historyPage + 1)}
                            >
                                <ChevronRight size={14} />
                            </button>
                        </div>
                    )}
                    {(isLive && wsStatus !== 'stream_ended') && activeTab === 'logs' && onStop && (
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

            {activeTab === 'logs' ? (
                <div className="term-body" ref={scrollRef} onScroll={handleScroll}>
                    {logs.length === 0 ? (
                        <div className="term-empty-state">
                            {(isLive && wsStatus !== 'stream_ended')
                                ? (wsStatus === 'connecting' ? '> Initializing connection...' : '> Waiting for task activity...')
                                : (logHistoryLoading ? '> Loading log history...' : '> No log records found.')
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
            ) : (
                <div className="term-data-content">
                    <div className="term-data-list" ref={scrollRef} onScroll={handleScroll}>
                        {displayData.length === 0 ? (
                            <div className="term-empty-state">
                                {isLive
                                    ? (dataWsStatus === 'connecting' ? 'Connecting...' : 'Waiting for data stream...')
                                    : (historyLoading ? 'Loading...' : 'No data collected yet.')
                                }
                            </div>
                        ) : (
                            displayData.map((item, idx) => (
                                <div
                                    key={item.id}
                                    className={classNames('term-data-item', { selected: selectedDataItem?.id === item.id })}
                                    onClick={() => setSelectedDataItem(selectedDataItem?.id === item.id ? null : item)}
                                >
                                    <div className="term-data-item-header">
                                        <span className="term-data-item-index">#{isLive ? wsData.length - idx : historyData.length - idx}</span>
                                        <span className="term-data-item-time">{item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : ''}</span>
                                    </div>
                                    <div className="term-data-item-preview">{getDataPreview(item.data)}</div>
                                </div>
                            ))
                        )}
                    </div>
                    {selectedDataItem && (
                        <div className="term-data-detail">
                            <div className="term-data-detail-header">
                                <FileCode size={14} />
                                <span>Detail</span>
                                <button className="term-data-detail-close" onClick={() => setSelectedDataItem(null)}>
                                    <X size={14} />
                                </button>
                            </div>
                            <pre className="term-data-detail-content">{formatJson(selectedDataItem.data)}</pre>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default LogTerminal;