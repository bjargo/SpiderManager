import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Database, RefreshCw, Loader2, Search, AlertTriangle, ShieldCheck,
    Clock, HardDrive, Hash, Table2, ChevronDown, ChevronUp, ListTree, X,
} from 'lucide-react';
import classNames from 'classnames';
import { fetchDataTables, fetchTableOverview, fetchTableRows } from '@/api/dataview';
import type {
    DataRowItem,
    DataTableOverviewResponse,
    DataTableSummary,
    QueryGuardInfo,
} from '@/types/dataview';
import './DataExplorerView.css';

// 前端最多累积渲染的行数，超出后提示用户重新开始，避免长列表拖慢页面
const MAX_ACCUMULATED_ROWS = 300;
const PAGE_SIZE_OPTIONS = [20, 50, 100];

// ── 格式化工具 ──
function fmtBytes(bytes: number): string {
    if (!bytes || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const idx = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / Math.pow(1024, idx);
    return `${value >= 100 || idx === 0 ? Math.round(value) : value.toFixed(1)} ${units[idx]}`;
}

function fmtNumber(value: number | null | undefined): string {
    if (value === null || value === undefined) return '-';
    return value.toLocaleString('en-US');
}

function fmtTime(iso: string | null | undefined): string {
    if (!iso) return '-';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '-';
    const p = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function dataPreview(data: Record<string, unknown>): string {
    const keys = Object.keys(data);
    if (keys.length === 0) return '{}';
    const preview = keys
        .slice(0, 3)
        .map(k => `${k}: ${String(data[k]).slice(0, 24)}`)
        .join('  ·  ');
    return keys.length > 3 ? `${preview}  ·  …` : preview;
}

function extractErrorMessage(err: unknown): string {
    const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (err instanceof Error) return err.message;
    return '请求失败';
}

// ── 数据行（memo 化：追加新行时已有行不重渲染）──
interface ExplorerRowProps {
    row: DataRowItem;
    selected: boolean;
    onSelect: (row: DataRowItem) => void;
}

const ExplorerRow = React.memo(({ row, selected, onSelect }: ExplorerRowProps) => (
    <div
        className={classNames('de-row', { selected })}
        onClick={() => onSelect(row)}
    >
        <div className="de-row-main">
            <span className="de-row-id">#{row.id}</span>
            <span className="de-row-task" title={row.task_id}>{row.task_id.slice(0, 8)}</span>
            <span className="de-row-time">{fmtTime(row.created_at)}</span>
        </div>
        <div className="de-row-preview">{dataPreview(row.data)}</div>
    </div>
));

/** 查询限制提示条：把后端的防护参数直接展示给使用者 */
function GuardNotice({ guard }: { guard: QueryGuardInfo | null }) {
    if (!guard) return null;
    return (
        <div className="de-guard" title={guard.note}>
            <ShieldCheck size={13} />
            <span>只读受限查询：单次 ≤ {guard.max_limit} 行 · 语句超时 {guard.statement_timeout_ms}ms · 并发 {guard.max_concurrent_queries} · 仅游标分页</span>
        </div>
    );
}

export default function DataExplorerView() {
    // ── 表清单 ──
    const [tables, setTables] = useState<DataTableSummary[]>([]);
    const [tablesLoading, setTablesLoading] = useState(false);
    const [tablesError, setTablesError] = useState<string | null>(null);
    const [guard, setGuard] = useState<QueryGuardInfo | null>(null);
    const [keyword, setKeyword] = useState('');

    const [activeTable, setActiveTable] = useState<string | null>(null);

    // ── 单表概况 ──
    const [overview, setOverview] = useState<DataTableOverviewResponse | null>(null);
    const [overviewLoading, setOverviewLoading] = useState(false);

    // ── 数据浏览 ──
    const [rows, setRows] = useState<DataRowItem[]>([]);
    const [rowsLoading, setRowsLoading] = useState(false);
    const [rowsError, setRowsError] = useState<string | null>(null);
    const [hasMore, setHasMore] = useState(false);
    const [cursor, setCursor] = useState<number | null>(null);
    const [pageSize, setPageSize] = useState(20);
    const [order, setOrder] = useState<'desc' | 'asc'>('desc');
    const [taskFilter, setTaskFilter] = useState<string>('');
    const [queryMs, setQueryMs] = useState<number | null>(null);
    const [detailRow, setDetailRow] = useState<DataRowItem | null>(null);

    const filteredTables = useMemo(() => {
        const kw = keyword.trim().toLowerCase();
        if (!kw) return tables;
        return tables.filter(t => t.table_name.toLowerCase().includes(kw));
    }, [tables, keyword]);

    // ── 加载表清单 ──
    const loadTables = useCallback(async () => {
        setTablesLoading(true);
        setTablesError(null);
        try {
            const res = await fetchDataTables();
            if (res?.code === 200 && res.data) {
                setTables(res.data.items);
                setGuard(res.data.guard);
                setActiveTable(prev => prev ?? res.data.items[0]?.table_name ?? null);
            }
        } catch (err) {
            setTablesError(extractErrorMessage(err));
        } finally {
            setTablesLoading(false);
        }
    }, []);

    useEffect(() => {
        loadTables();
    }, [loadTables]);

    // ── 加载单表概况 ──
    const loadOverview = useCallback(async (tableName: string) => {
        setOverviewLoading(true);
        try {
            const res = await fetchTableOverview(tableName);
            if (res?.code === 200 && res.data) {
                setOverview(res.data);
                setGuard(res.data.guard);
            }
        } catch (err) {
            setOverview(null);
            setRowsError(extractErrorMessage(err));
        } finally {
            setOverviewLoading(false);
        }
    }, []);

    // ── 加载数据行（游标分页）──
    const loadRows = useCallback(
        async (tableName: string, options: { append?: boolean; nextCursor?: number | null } = {}) => {
            setRowsLoading(true);
            setRowsError(null);
            try {
                const res = await fetchTableRows(tableName, {
                    limit: pageSize,
                    cursor: options.nextCursor ?? null,
                    task_id: taskFilter || null,
                    order,
                });
                if (res?.code === 200 && res.data) {
                    const incoming = res.data.items;
                    setRows(prev => {
                        const merged = options.append ? [...prev, ...incoming] : incoming;
                        return merged.length > MAX_ACCUMULATED_ROWS
                            ? merged.slice(merged.length - MAX_ACCUMULATED_ROWS)
                            : merged;
                    });
                    setCursor(res.data.next_cursor);
                    setHasMore(res.data.has_more);
                    setQueryMs(res.data.query_ms);
                }
            } catch (err) {
                setRowsError(extractErrorMessage(err));
                if (!options.append) setRows([]);
                setHasMore(false);
            } finally {
                setRowsLoading(false);
            }
        },
        [pageSize, taskFilter, order],
    );

    // 切换表：重置并加载
    useEffect(() => {
        if (!activeTable) return;
        setRows([]);
        setCursor(null);
        setHasMore(false);
        setDetailRow(null);
        setTaskFilter('');
        setQueryMs(null);
        loadOverview(activeTable);
    }, [activeTable, loadOverview]);

    useEffect(() => {
        if (!activeTable) return;
        loadRows(activeTable, { append: false });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeTable, pageSize, order, taskFilter]);

    const handleRefresh = () => {
        loadTables();
        if (activeTable) {
            loadOverview(activeTable);
            setRows([]);
            setCursor(null);
            loadRows(activeTable, { append: false });
        }
    };

    const handleLoadMore = () => {
        if (!activeTable || !cursor) return;
        loadRows(activeTable, { append: true, nextCursor: cursor });
    };

    const handleReset = () => {
        if (!activeTable) return;
        setRows([]);
        setCursor(null);
        setDetailRow(null);
        loadRows(activeTable, { append: false });
    };

    const activeSummary = tables.find(t => t.table_name === activeTable) ?? null;

    return (
        <div className="de-container">
            {/* 工具栏 */}
            <div className="de-toolbar glass-panel">
                <div className="de-toolbar-left">
                    <h2><Database size={20} /> 数据查看</h2>
                    <span className="de-count">{tables.length} 张数据表</span>
                    <span className="de-readonly-badge">READ-ONLY</span>
                </div>
                <div className="de-toolbar-right">
                    <GuardNotice guard={guard} />
                    <button className="de-btn" onClick={handleRefresh} disabled={tablesLoading}>
                        {tablesLoading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
                        刷新
                    </button>
                </div>
            </div>

            <div className="de-layout">
                {/* 左：表清单 */}
                <aside className="de-tables glass-panel">
                    <div className="de-search">
                        <Search size={14} />
                        <input
                            value={keyword}
                            onChange={e => setKeyword(e.target.value)}
                            placeholder="搜索数据表…"
                        />
                    </div>

                    {tablesError && (
                        <div className="de-error"><AlertTriangle size={14} /> {tablesError}</div>
                    )}

                    <div className="de-table-list">
                        {tablesLoading && tables.length === 0 && (
                            <div className="de-empty"><Loader2 size={16} className="spin" /> 加载中…</div>
                        )}
                        {!tablesLoading && filteredTables.length === 0 && (
                            <div className="de-empty">暂无数据表</div>
                        )}
                        {filteredTables.map(t => (
                            <button
                                key={t.table_name}
                                className={classNames('de-table-item', { active: t.table_name === activeTable })}
                                onClick={() => setActiveTable(t.table_name)}
                            >
                                <div className="de-table-item-top">
                                    <Table2 size={14} />
                                    <span className="de-table-name" title={t.table_name}>{t.table_name}</span>
                                </div>
                                <div className="de-table-item-meta">
                                    <span><Hash size={11} /> {fmtNumber(t.estimated_rows)}</span>
                                    <span><HardDrive size={11} /> {fmtBytes(t.table_bytes)}</span>
                                    <span><Clock size={11} /> {t.last_collected_at ? fmtTime(t.last_collected_at).slice(5, 16) : '未采集'}</span>
                                </div>
                            </button>
                        ))}
                    </div>
                </aside>

                {/* 右：详情 */}
                <section className="de-detail glass-panel">
                    {!activeTable ? (
                        <div className="de-empty-large"><Database size={40} strokeWidth={1} /> 请选择左侧数据表</div>
                    ) : (
                        <>
                            <div className="de-detail-header">
                                <div className="de-detail-title">
                                    <Table2 size={16} />
                                    <span>{activeTable}</span>
                                    {activeSummary?.spider_id !== null && activeSummary?.spider_id !== undefined && (
                                        <span className="de-badge-spider">爬虫 #{activeSummary.spider_id}</span>
                                    )}
                                    {overviewLoading && <Loader2 size={14} className="spin" />}
                                </div>
                                <div className="de-detail-meta">
                                    {queryMs !== null && <span className="de-query-ms">上次查询 {queryMs.toFixed(1)}ms</span>}
                                </div>
                            </div>

                            {/* 采集概况 */}
                            <div className="de-stats">
                                <div className="de-stat">
                                    <span className="de-stat-label">估算记录数</span>
                                    <span className="de-stat-value">{fmtNumber(overview?.estimated_rows ?? activeSummary?.estimated_rows)}</span>
                                    <span className="de-stat-hint">来自数据库统计信息</span>
                                </div>
                                <div className="de-stat">
                                    <span className="de-stat-label">占用空间</span>
                                    <span className="de-stat-value">{fmtBytes(overview?.table_bytes ?? activeSummary?.table_bytes ?? 0)}</span>
                                    <span className="de-stat-hint">含索引</span>
                                </div>
                                <div className="de-stat">
                                    <span className="de-stat-label">采集任务</span>
                                    <span className="de-stat-value">{fmtNumber(overview?.task_count ?? activeSummary?.task_count)}</span>
                                    <span className="de-stat-hint">成功 {fmtNumber(overview?.success_count ?? activeSummary?.success_count)} 次</span>
                                </div>
                                <div className="de-stat">
                                    <span className="de-stat-label">最近采集</span>
                                    <span className="de-stat-value de-stat-value-sm">{fmtTime(overview?.last_collected_at ?? activeSummary?.last_collected_at)}</span>
                                    <span className="de-stat-hint">
                                        {overview?.has_task_index === false ? '缺少 _task_id 索引，按任务过滤会变慢' : '按任务过滤走索引'}
                                    </span>
                                </div>
                            </div>

                            {/* 字段预览 */}
                            {overview && overview.sample_fields.length > 0 && (
                                <div className="de-fields">
                                    <div className="de-section-label"><ListTree size={13} /> 字段预览（采样最新 {overview.sampled_rows} 条）</div>
                                    <div className="de-field-chips">
                                        {overview.sample_fields.map(f => (
                                            <span key={f} className="de-chip">{f}</span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* 数据浏览 */}
                            <div className="de-data-toolbar">
                                <div className="de-data-toolbar-left">
                                    <label className="de-inline-field">
                                        任务
                                        <select
                                            value={taskFilter}
                                            onChange={e => setTaskFilter(e.target.value)}
                                            className="de-select"
                                        >
                                            <option value="">全部任务</option>
                                            {(overview?.recent_tasks ?? []).map(t => (
                                                <option key={t.task_id} value={t.task_id}>
                                                    {t.task_id.slice(0, 8)} · {t.status}
                                                </option>
                                            ))}
                                        </select>
                                    </label>
                                    <label className="de-inline-field">
                                        每页
                                        <select
                                            value={pageSize}
                                            onChange={e => setPageSize(Number(e.target.value))}
                                            className="de-select de-select-sm"
                                        >
                                            {PAGE_SIZE_OPTIONS.map(size => (
                                                <option key={size} value={size}>{size}</option>
                                            ))}
                                        </select>
                                    </label>
                                    <button
                                        className="de-btn de-btn-ghost"
                                        onClick={() => setOrder(order === 'desc' ? 'asc' : 'desc')}
                                        title="切换排序方向"
                                    >
                                        {order === 'desc' ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
                                        {order === 'desc' ? '最新优先' : '最早优先'}
                                    </button>
                                </div>
                                <div className="de-data-toolbar-right">
                                    <span className="de-row-count">
                                        已显示 {rows.length} 行
                                        {rows.length >= MAX_ACCUMULATED_ROWS && '（已达前端展示上限，可「重新开始」）'}
                                    </span>
                                    <button className="de-btn de-btn-ghost" onClick={handleReset} disabled={rowsLoading}>
                                        <RefreshCw size={14} /> 重新开始
                                    </button>
                                </div>
                            </div>

                            {rowsError && (
                                <div className="de-error"><AlertTriangle size={14} /> {rowsError}</div>
                            )}

                            <div className="de-rows">
                                {rowsLoading && rows.length === 0 && (
                                    <div className="de-empty-large"><Loader2 size={18} className="spin" /> 查询中…</div>
                                )}
                                {!rowsLoading && rows.length === 0 && !rowsError && (
                                    <div className="de-empty-large">暂无数据</div>
                                )}
                                {rows.map(row => (
                                    <ExplorerRow
                                        key={row.id}
                                        row={row}
                                        selected={detailRow?.id === row.id}
                                        onSelect={setDetailRow}
                                    />
                                ))}
                            </div>

                            <div className="de-footer">
                                <button
                                    className="de-btn"
                                    onClick={handleLoadMore}
                                    disabled={!hasMore || rowsLoading}
                                >
                                    {rowsLoading && rows.length > 0 ? <Loader2 size={14} className="spin" /> : <ChevronDown size={14} />}
                                    加载更多
                                </button>
                                {!rowsLoading && !hasMore && rows.length > 0 && <span className="de-footer-hint">已到头</span>}
                                {!rowsLoading && !hasMore && rows.length === 0 && !rowsError && (
                                    <span className="de-footer-hint">该筛选条件下没有数据</span>
                                )}
                            </div>
                        </>
                    )}
                </section>
            </div>

            {/* JSON 详情 */}
            {detailRow && (
                <div className="de-json-panel glass-panel">
                    <div className="de-json-header">
                        <span>记录 #{detailRow.id}</span>
                        <span className="de-json-task">任务 {detailRow.task_id}</span>
                        <button className="de-json-close" onClick={() => setDetailRow(null)} title="关闭">
                            <X size={14} />
                        </button>
                    </div>
                    <pre className="de-json-body">{JSON.stringify(detailRow.data, null, 2)}</pre>
                </div>
            )}
        </div>
    );
}
