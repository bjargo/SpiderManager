/** 数据查看模块类型定义 */

export interface QueryGuardInfo {
    max_limit: number;
    default_limit: number;
    statement_timeout_ms: number;
    max_concurrent_queries: number;
    metadata_cache_ttl_seconds: number;
    pagination: string;
    note: string;
}

export interface DataTableSummary {
    table_name: string;
    estimated_rows: number;
    table_bytes: number;
    spider_id: number | null;
    description: string | null;
    task_count: number;
    success_count: number;
    last_collected_at: string | null;
    last_task_id: string | null;
}

export interface DataTableListResponse {
    items: DataTableSummary[];
    total_tables: number;
    guard: QueryGuardInfo;
    generated_at: string;
}

export interface RecentTaskItem {
    task_id: string;
    status: string;
    started_at: string | null;
    finished_at: string | null;
}

export interface DataTableOverviewResponse {
    table_name: string;
    estimated_rows: number;
    table_bytes: number;
    task_count: number;
    success_count: number;
    last_collected_at: string | null;
    sample_fields: string[];
    sampled_rows: number;
    has_task_index: boolean;
    recent_tasks: RecentTaskItem[];
    guard: QueryGuardInfo;
}

export interface DataRowItem {
    id: number;
    task_id: string;
    data: Record<string, unknown>;
    created_at: string | null;
}

export interface DataRowsResponse {
    table_name: string;
    items: DataRowItem[];
    limit: number;
    next_cursor: number | null;
    has_more: boolean;
    estimated_total: number;
    exact_total: number | null;
    order: string;
    query_ms: number;
    guard: QueryGuardInfo;
}
