import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
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

// 窗口化渲染上限：单次最多挂载的日志行 / 数据条数，避免上万 DOM 节点造成卡顿
const MAX_RENDERED_LOGS = 1200;
const MAX_RENDERED_DATA = 200;

/** 格式化 JSON 显示 */
const formatJson = (data: Record<string, any>): string => JSON.stringify(data, null, 2);

/** 获取数据字段预览 */
const getDataPreview = (data: Record<string, any>): string => {
    const keys = Object.keys(data);
    if (keys.length === 0) return '{}';
    const preview = keys.slice(0, 3).map(k => `${k}: ${String(data[k]).substring(0, 20)}`).join(', ');
    return keys.length > 3 ? `${preview}...` : preview;
};

/** 单行日志：memo 化后，追加新日志不会导致已有行重新渲染 */
const LogLine = React.memo(({ text }: { text: string }) => (
    <div className="log-line">
        <span style={{ color: 'var(--code-text-muted)', marginRight: '8px' }}>$</span>
        {text}
    </div>
));

interface DataRowProps {
    item: DataItem;
    indexLabel: string;
    selected: boolean;
    onToggle: (item: DataItem) => void;
}

/** 单条数据卡片：预览与时间格式化成本较高，memo 化避免整列表重算 */
const DataRow = React.memo(({ item, indexLabel, selected, onToggle }: DataRowProps) => (
    <div
        className={classNames('term-data-item', { selected })}
        onClick={() => onToggle(item)}
    >
        <div className="term-data-item-header">
            <span className="term-data-item-index">#{indexLabel}</span>
            <span className="term-data-item-time">
                {item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : ''}
            </span>
        </div>
        <div className="term-data-item-preview">{getDataPreview(item.data)}</div>
    </div>
));

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

    const { logs: wsLogs, status: wsStatus, clearLogs } = useLogSocket(
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
    // 上滚时固定的窗口末端：新数据不再推动视口内容
    const logPinEndRef = useRef<number | null>(null);
    const dataPinEndRef = useRef<number | null>(null);
    const pendingScrollAdjustRef = useRef<{ scrollTop: number; scrollHeight: number } | null>(null);
    const [logWindowSize, setLogWindowSize] = useState(MAX_RENDERED_LOGS);
    const [dataWindowSize, setDataWindowSize] = useState(MAX_RENDERED_DATA);

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

    // 窗口化渲染：只挂载列表尾部固定数量的节点。
    // 用户上滚时固定窗口末端（新数据不推动视口内容），触顶时向前追加更多行。
    const logWindow = useMemo(() => {
        const pinnedEnd = logPinEndRef.current;
        const end = pinnedEnd === null ? logs.length : Math.min(pinnedEnd, logs.length);
        const start = Math.max(0, end - logWindowSize);
        return { items: logs.slice(start, end), hidden: start };
    }, [logs, logWindowSize]);

    const dataWindow = useMemo(() => {
        const pinnedEnd = dataPinEndRef.current;
        const end = pinnedEnd === null ? displayData.length : Math.min(pinnedEnd, displayData.length);
        const start = Math.max(0, end - dataWindowSize);
        return { items: displayData.slice(start, end), start, hidden: start };
    }, [displayData, dataWindowSize]);

    const handleToggleData = useCallback((item: DataItem) => {
        setSelectedDataItem(prev => (prev?.id === item.id ? null : item));
    }, []);

    // 数据行元素：仅新增条目或选中项变化时才重建，其余行由 memo 复用
    const dataRows = useMemo(() => dataWindow.items.map((item, idx) => {
        const absoluteIdx = dataWindow.start + idx;
        return (
            <DataRow
                key={item.id}
                item={item}
                indexLabel={isLive ? String(item.seq ?? absoluteIdx + 1) : String(historyData.length - absoluteIdx)}
                selected={selectedDataItem?.id === item.id}
                onToggle={handleToggleData}
            />
        );
    }), [dataWindow, isLive, historyData.length, selectedDataItem?.id, handleToggleData]);

    // Auto scroll logic
    useEffect(() => {
        if (autoScroll && scrollRef.current && activeTab === 'logs') {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [logs, autoScroll, activeTab]);

    // 向前追加窗口后修正滚动位置，保证视口内容不跳动
    useLayoutEffect(() => {
        const pending = pendingScrollAdjustRef.current;
        pendingScrollAdjustRef.current = null;
        const el = scrollRef.current;
        if (!pending || !el) return;
        el.scrollTop = pending.scrollTop + (el.scrollHeight - pending.scrollHeight);
    }, [logWindowSize, dataWindowSize]);

    const handleScroll = () => {
        const el = scrollRef.current;
        if (!el) return;
        const isLogTab = activeTab === 'logs';
        const { scrollTop, scrollHeight, clientHeight } = el;
        const atBottom = scrollHeight - scrollTop - clientHeight <= 30;
        setAutoScroll(atBottom);

        const pinRef = isLogTab ? logPinEndRef : dataPinEndRef;
        const total = isLogTab ? logs.length : displayData.length;
        const windowSize = isLogTab ? logWindowSize : dataWindowSize;
        const baseSize = isLogTab ? MAX_RENDERED_LOGS : MAX_RENDERED_DATA;

        if (atBottom) {
            // 回到尾部：恢复默认窗口并跟随最新内容
            pinRef.current = null;
            if (windowSize !== baseSize) {
                if (isLogTab) setLogWindowSize(baseSize);
                else setDataWindowSize(baseSize);
            }
            return;
        }

        if (pinRef.current === null) {
            pinRef.current = total;
        }

        // 触顶时向前加载更多，避免看不到更早的内容
        if (scrollTop <= 40 && (pinRef.current ?? total) - windowSize > 0) {
            pendingScrollAdjustRef.current = { scrollTop, scrollHeight };
            if (isLogTab) setLogWindowSize(size => size + MAX_RENDERED_LOGS);
            else setDataWindowSize(size => size + MAX_RENDERED_DATA);
        }
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
                        onClick={() => { logPinEndRef.current = null; setActiveTab('logs'); }}
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
                        onClick={() => { dataPinEndRef.current = null; setActiveTab('data'); }}
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
                            <button className="term-btn" title="Clear Logs" onClick={clearLogs}>
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
                            {logWindow.hidden > 0 && (
                                <div className="log-line" style={{ color: 'var(--text-muted)' }}>
                                    … {logWindow.hidden} earlier line(s) hidden — scroll up to load more
                                </div>
                            )}
                            {logWindow.items.map(log => (
                                <LogLine key={log.id} text={log.text} />
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
                            <>
                                {dataWindow.hidden > 0 && (
                                    <div style={{ padding: '8px 12px', color: 'var(--text-muted)', fontSize: '12px' }}>
                                        … {dataWindow.hidden} earlier record(s) hidden — scroll up to load more
                                    </div>
                                )}
                                {dataRows}
                            </>
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

export default React.memo(LogTerminal);