import { useState, useEffect, useRef } from 'react';

export interface LogItem {
    id: string;
    text: string;
}

export function useLogSocket(taskId: string | null) {
    const [logs, setLogs] = useState<LogItem[]>([]);
    const [status, setStatus] = useState<'idle' | 'connecting' | 'connected' | 'error' | 'closed'>('idle');
    const wsRef = useRef<WebSocket | null>(null);

    useEffect(() => {
        // 每次 taskId 变化时清空旧日志
        setLogs([]);

        if (!taskId) {
            setStatus('idle');
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws-logs/${taskId}`;

        let reconnectTimer: ReturnType<typeof setTimeout>;
        let isMounted = true;
        let wasConnected = false;

        const connect = () => {
            console.log(`[WS] Connecting to ${wsUrl}...`);
            setStatus('connecting');
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            ws.onopen = () => {
                console.log(`[WS] Connected to ${taskId}`);
                wasConnected = true;
                if (isMounted) setStatus('connected');
            };

            ws.onmessage = (event) => {
                if (!isMounted) return;
                setLogs(prev => {
                    const updated = [...prev, {
                        id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(),
                        text: event.data
                    }];
                    if (updated.length > 5000) {
                        return updated.slice(updated.length - 5000);
                    }
                    return updated;
                });
            };

            ws.onerror = (err) => {
                console.error(`[WS] Connection error for ${taskId}:`, err);
                if (isMounted) setStatus('error');
            };

            ws.onclose = (event) => {
                console.warn(`[WS] Connection closed for ${taskId}, code: ${event.code}`);
                if (isMounted) {
                    setStatus('closed');
                    // 仅在曾经成功连接后才自动重连（避免无效重试）
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
    }, [taskId]);

    return { logs, status, setLogs };
}
