import request from '@/utils/request';
import type { ApiResponse } from '@/types/api';
import type {
    DataRowsResponse,
    DataTableListResponse,
    DataTableOverviewResponse,
} from '@/types/dataview';

/** 数据表清单与采集概况（后端带 30s 元数据缓存） */
export const fetchDataTables = (): Promise<ApiResponse<DataTableListResponse>> => {
    return request.get('/dataview/tables');
};

/** 单表采集概况（记录数估算、字段预览、最近任务） */
export const fetchTableOverview = (tableName: string): Promise<ApiResponse<DataTableOverviewResponse>> => {
    return request.get(`/dataview/tables/${encodeURIComponent(tableName)}`);
};

export interface TableRowsParams {
    limit?: number;
    /** 游标：上一页最后一行的 _id */
    cursor?: number | null;
    /** 按任务 ID 精确过滤（走索引） */
    task_id?: string | null;
    order?: 'desc' | 'asc';
}

/** 游标分页浏览表数据（单次最多 100 行，不支持跳页） */
export const fetchTableRows = (
    tableName: string,
    params: TableRowsParams = {},
): Promise<ApiResponse<DataRowsResponse>> => {
    const query: Record<string, string | number> = {};
    if (params.limit) query.limit = params.limit;
    if (params.cursor) query.cursor = params.cursor;
    if (params.task_id) query.task_id = params.task_id;
    if (params.order) query.order = params.order;
    return request.get(`/dataview/tables/${encodeURIComponent(tableName)}/rows`, { params: query });
};
