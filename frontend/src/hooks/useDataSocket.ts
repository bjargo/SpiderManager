import { useState, useEffect, useRef } from 'react';

export interface DataItem {
    id: string;
    data: Record<string, any>;
    timestamp?: string;
}

interface UseDataSocketOptions {
    maxItems?: number;
    onMessage?: (data: DataItem) => void;
}

export function useDataSocket(taskId: string | null, options?: UseDataSocketOptions) {
    const [data, setData] = useState<DataItem[]>([]);
    const [status, setStatus] = useState<'idle' | 'connecting' | 'connected' | 'error' | 'closed'>('idle');
    const [totalCount, setTotalCount] = useState(0);
    const wsRef = useRef<WebSocket | null>(null);
    const maxItems = options?.maxItems || 500;

    useEffect(() => {
        setData([]);
        setTotalCount(0);

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
                    const newItem: DataItem = {
                        id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(),
                        data: rawMsg.d || rawMsg.data || rawMsg,
                        timestamp: rawMsg.ts || new Date().toISOString(),
                    };
                    
                    setData(prev => {
                        const updated = [...prev, newItem];
                        if (updated.length > maxItems) {
                            return updated.slice(updated.length - maxItems);
                        }
                        return updated;
                    });
                    
                    setTotalCount(prev => prev + 1);
                    options?.onMessage?.(newItem);
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
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [taskId, maxItems]);

    const clearData = () => {
        setData([]);
        setTotalCount(0);
    };

    return { data, status, totalCount, clearData };
}