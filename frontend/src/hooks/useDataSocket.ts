import { useCallback, useEffect, useRef, useState } from 'react';

export interface DataItem {
    id: string;
    // 单调递增序号：窗口化渲染时作为稳定编号，避免随缓冲区裁剪漂移
    seq?: number;
    data: Record<string, any>;
    timestamp?: string;
}

interface UseDataSocketOptions {
    maxItems?: number;
    onMessage?: (data: DataItem) => void;
}

// 数据刷新节流间隔：合并该时间窗内到达的多条消息，只触发一次 state 更新
const FLUSH_INTERVAL_MS = 100;

type DataSocketStatus = 'idle' | 'connecting' | 'connected' | 'error' | 'closed';

export function useDataSocket(taskId: string | null, options?: UseDataSocketOptions) {
    const [data, setData] = useState<DataItem[]>([]);
    const [status, setStatus] = useState<DataSocketStatus>('idle');
    const [totalCount, setTotalCount] = useState(0);
    const wsRef = useRef<WebSocket | null>(null);
    const maxItems = options?.maxItems || 500;

    const pendingRef = useRef<DataItem[]>([]);
    const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const seqRef = useRef(0);
    const maxItemsRef = useRef(maxItems);
    maxItemsRef.current = maxItems;
    const onMessageRef = useRef(options?.onMessage);
    onMessageRef.current = options?.onMessage;

    const flush = useCallback(() => {
        if (flushTimerRef.current !== null) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
        }
        const pending = pendingRef.current;
        if (pending.length === 0) return;
        pendingRef.current = [];
        const limit = maxItemsRef.current;
        setData(prev => {
            const merged = prev.concat(pending);
            return merged.length > limit ? merged.slice(merged.length - limit) : merged;
        });
        setTotalCount(prev => prev + pending.length);
        const onMessage = onMessageRef.current;
        if (onMessage) {
            pending.forEach(item => onMessage(item));
        }
    }, []);

    const scheduleFlush = useCallback(() => {
        if (flushTimerRef.current !== null) return;
        flushTimerRef.current = setTimeout(flush, FLUSH_INTERVAL_MS);
    }, [flush]);

    useEffect(() => {
        setData([]);
        setTotalCount(0);
        pendingRef.current = [];
        if (flushTimerRef.current !== null) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
        }

        if (!taskId) {
            setStatus('idle');
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        const wsUrl = `${protocol}//${host}/ws-data/${taskId}`;

        let reconnectTimer: ReturnType<typeof setTimeout>;
        let isMounted = true;
        let wasConnected = false;

        const connect = () => {
            console.log(`[DataWS] Connecting to ${wsUrl}...`);
            setStatus('connecting');
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            ws.onopen = () => {
                console.log(`[DataWS] Connected for task ${taskId}`);
                wasConnected = true;
                if (isMounted) setStatus('connected');
            };

            ws.onmessage = (event) => {
                if (!isMounted) return;
                try {
                    const rawMsg = JSON.parse(event.data);
                    const rawData = rawMsg.d || rawMsg.data || rawMsg;
                    const timestamp = rawMsg.ts || new Date().toISOString();
                    // data_reducer 发布的 d 字段可能是数组（批量数据），需要拆分为独立 DataItem
                    const rows: any[] = Array.isArray(rawData) ? rawData : [rawData];
                    const pending = pendingRef.current;

                    for (const row of rows) {
                        const seq = ++seqRef.current;
                        pending.push({
                            id: String(seq),
                            seq,
                            data: typeof row === 'object' && row !== null ? row : { value: row },
                            timestamp,
                        });
                    }
                    scheduleFlush();
                } catch (e) {
                    console.warn('[DataWS] Failed to parse message:', e);
                }
            };

            ws.onerror = (err) => {
                console.error(`[DataWS] Connection error for ${taskId}:`, err);
                if (isMounted) setStatus('error');
            };

            ws.onclose = (event) => {
                console.warn(`[DataWS] Connection closed for ${taskId}, code: ${event.code}`);
                if (isMounted) {
                    setStatus('closed');
                    if (wasConnected) {
                        reconnectTimer = setTimeout(connect, 3000);
                    }
                }
            };
        };

        connect();

        return () => {
            isMounted = false;
            clearTimeout(reconnectTimer);
            if (flushTimerRef.current !== null) {
                clearTimeout(flushTimerRef.current);
                flushTimerRef.current = null;
            }
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [taskId, scheduleFlush]);

    const clearData = () => {
        pendingRef.current = [];
        setData([]);
        setTotalCount(0);
    };

    return { data, status, totalCount, clearData };
}