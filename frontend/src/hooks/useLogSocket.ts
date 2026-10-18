import { useCallback, useEffect, useRef, useState } from 'react';

export interface LogItem {
    id: string;
    text: string;
}

interface UseLogSocketOptions {
    onStreamEnd?: () => void;
}

// 日志缓冲区上限：仅保留最近 MAX_LOGS 行
const MAX_LOGS = 5000;
// 日志刷新节流间隔：合并该时间窗内到达的多条日志，只触发一次 state 更新
const FLUSH_INTERVAL_MS = 100;

type LogSocketStatus = 'idle' | 'connecting' | 'connected' | 'error' | 'closed' | 'stream_ended';

export function useLogSocket(taskId: string | null, options?: UseLogSocketOptions) {
    const [logs, setLogs] = useState<LogItem[]>([]);
    const [status, setStatus] = useState<LogSocketStatus>('idle');
    const wsRef = useRef<WebSocket | null>(null);

    // 待提交的日志：WS 每条消息只入队，由定时器批量合并后再 setState
    const pendingRef = useRef<LogItem[]>([]);
    const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const seqRef = useRef(0);
    const streamEndedRef = useRef(false);
    const onStreamEndRef = useRef(options?.onStreamEnd);
    onStreamEndRef.current = options?.onStreamEnd;

    const flush = useCallback(() => {
        if (flushTimerRef.current !== null) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
        }
        const pending = pendingRef.current;
        if (pending.length === 0) return;
        pendingRef.current = [];
        setLogs(prev => {
            const merged = prev.concat(pending);
            return merged.length > MAX_LOGS ? merged.slice(merged.length - MAX_LOGS) : merged;
        });
    }, []);

    const scheduleFlush = useCallback(() => {
        if (flushTimerRef.current !== null) return;
        flushTimerRef.current = setTimeout(flush, FLUSH_INTERVAL_MS);
    }, [flush]);

    const clearLogs = useCallback(() => {
        pendingRef.current = [];
        setLogs([]);
    }, []);

    useEffect(() => {
        // 每次 taskId 变化时清空旧日志与待提交队列
        setLogs([]);
        pendingRef.current = [];
        streamEndedRef.current = false;
        if (flushTimerRef.current !== null) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
        }

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
                // 检测流结束哨兵：不追加到日志，触发回调通知组件切换到历史模式
                if (event.data === '[STREAM_END]') {
                    streamEndedRef.current = true;
                    flush();
                    setStatus('stream_ended');
                    ws.close();
                    onStreamEndRef.current?.();
                    return;
                }
                pendingRef.current.push({ id: String(++seqRef.current), text: event.data });
                scheduleFlush();
            };

            ws.onerror = (err) => {
                console.error(`[WS] Connection error for ${taskId}:`, err);
                if (isMounted) setStatus('error');
            };

            ws.onclose = () => {
                if (!isMounted) return;
                // 哨兵触发的正常关闭不重连，也不覆盖 stream_ended 状态
                if (streamEndedRef.current) return;
                setStatus('closed');
                // 仅在曾经成功连接后才自动重连（避免无效重试）
                if (wasConnected) {
                    reconnectTimer = setTimeout(connect, 3000);
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
    }, [taskId, flush, scheduleFlush]);

    return { logs, status, clearLogs };
}
