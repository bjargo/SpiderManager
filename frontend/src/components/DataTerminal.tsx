import React, { useEffect, useRef, useState } from 'react';
import { useDataSocket, DataItem } from '@/hooks/useDataSocket';
import { fetchTaskData } from '@/api/task';
import { Database, XCircle, RefreshCw, X, History, ChevronLeft, ChevronRight, FileCode } from 'lucide-react';
import classNames from 'classnames';
import './DataTerminal.css';

interface Props {
    taskId: string | null;
    taskStatus?: string;
    onClose?: () => void;
}

const DataTerminal: React.FC<Props> = ({ taskId, taskStatus, onClose }) => {
    const isLive = taskStatus === 'running' || taskStatus === 'pending';

    // --- WebSocket 实时数据 (running/pending 时启用) ---
    const { data: wsData, status: wsStatus, totalCount: wsTotalCount, clearData } = useDataSocket(
        isLive ? taskId : null
    );

    // --- HTTP 历史数据 (非 live 时使用) ---
    const [historyData, setHistoryData] = useState<DataItem[]>([]);
    const [historyTotal, setHistoryTotal] = useState(0);
    const [historyLoading, setHistoryLoading] = useState(false);
    const [historyPage, setHistoryPage] = useState(0);
    const historyPageSize = 50;

    const scrollRef = useRef<HTMLDivElement>(null);
    const [autoScroll, setAutoScroll] = useState(true);
    const [selectedItem, setSelectedItem] = useState<DataItem | null>(null);

    // 加载历史数据
    const loadHistory = async (tid: string, page: number = 0) => {
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

    // 非 live 状态时加载历史数据
    useEffect(() => {
        if (!taskId || isLive) {
            setHistoryData([]);
            setHistoryTotal(0);
            setHistoryPage(0);
            return;
        }
        loadHistory(taskId, 0);
    }, [taskId, isLive]);

    // 当前显示的数据
    const displayData = isLive ? wsData : historyData;

    // Auto scroll logic
    useEffect(() => {
        if (autoScroll && scrollRef.current && isLive) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [displayData, autoScroll, isLive]);

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

    const renderStatusText = () => {
        if (!isLive) {
            if (historyLoading) return 'Loading...';
            return `History (${historyTotal} records)`;
        }
        switch (wsStatus) {
            case 'connecting': return 'Reconnecting...';
            case 'connected': return `Streaming (${wsTotalCount} records)`;
            case 'error': return 'Connection Error';
            case 'closed': return 'Disconnected';
            default: return wsStatus;
        }
    };

    const statusClass = isLive ? wsStatus : 'history';

    // 分页控制
    const totalPages = Math.ceil(historyTotal / historyPageSize);
    const canPrev = historyPage > 0;
    const canNext = historyPage < totalPages - 1;

    return (
        <div className="data-terminal-wrapper glass-panel animate-fade-in">
            <div className="data-term-header">
                <div className="data-term-info">
                    {isLive ? <Database size={16} /> : <History size={16} />}
                    <span>{isLive ? 'Real-time Data Stream' : 'Collected Data'}</span>
                    <div className={classNames('data-term-status', statusClass)}>
                        {isLive && wsStatus === 'connected' && <div className="status-dot-blink" />}
                        <span style={{ fontSize: '10px', fontWeight: 600 }}>{renderStatusText()}</span>
                    </div>
                </div>
                <div className="data-term-actions">
                    {isLive && (
                        <>
                            <button
                                className="data-term-btn"
                                title={autoScroll ? 'Disable Auto-scroll' : 'Enable Auto-scroll'}
                                onClick={() => setAutoScroll(!autoScroll)}
                                style={{ color: autoScroll ? 'var(--accent-primary)' : 'inherit' }}
                            >
                                <RefreshCw size={14} className={classNames({ spin: wsStatus === 'connecting' })} />
                            </button>
                            <button className="data-term-btn" title="Clear Data" onClick={clearData}>
                                <XCircle size={14} />
                            </button>
                        </>
                    )}
                    {!isLive && historyTotal > 0 && (
                        <div className="data-pagination">
                            <button
                                className="data-page-btn"
                                disabled={!canPrev || historyLoading}
                                onClick={() => loadHistory(taskId, historyPage - 1)}
                            >
                                <ChevronLeft size={14} />
                            </button>
                            <span className="data-page-info">
                                {historyPage + 1} / {totalPages}
                            </span>
                            <button
                                className="data-page-btn"
                                disabled={!canNext || historyLoading}
                                onClick={() => loadHistory(taskId, historyPage + 1)}
                            >
                                <ChevronRight size={14} />
                            </button>
                        </div>
                    )}
                    {onClose && (
                        <button className="data-term-btn close" title="Close" onClick={onClose}>
                            <X size={16} />
                        </button>
                    )}
                </div>
            </div>

            <div className="data-term-content">
                <div className="data-term-list" ref={scrollRef} onScroll={handleScroll}>
                    {displayData.length === 0 ? (
                        <div className="data-term-empty">
                            {isLive
                                ? (wsStatus === 'connecting' ? 'Connecting...' : 'Waiting for data stream...')
                                : (historyLoading ? 'Loading...' : 'No data collected yet.')
                            }
                        </div>
                    ) : (
                        displayData.map((item, idx) => (
                            <div
                                key={item.id}
                                className={classNames('data-item', { selected: selectedItem?.id === item.id })}
                                onClick={() => setSelectedItem(selectedItem?.id === item.id ? null : item)}
                            >
                                <div className="data-item-header">
                                    <span className="data-item-index">#{isLive ? wsData.length - idx : historyData.length - idx}</span>
                                    <span className="data-item-time">{item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : ''}</span>
                                </div>
                                <div className="data-item-preview">{getDataPreview(item.data)}</div>
                            </div>
                        ))
                    )}
                </div>

                {/* 详情面板 */}
                {selectedItem && (
                    <div className="data-detail-panel">
                        <div className="data-detail-header">
                            <FileCode size={14} />
                            <span>Data Detail</span>
                            <button className="data-detail-close" onClick={() => setSelectedItem(null)}>
                                <X size={14} />
                            </button>
                        </div>
                        <pre className="data-detail-content">{formatJson(selectedItem.data)}</pre>
                    </div>
                )}
            </div>
        </div>
    );
};

export default DataTerminal;