import type { SpiderNode } from './node';

export type SourceType = 'MINIO' | 'GIT';

export interface SpiderItem {
    id: number;
    name: string;
    description: string | null;
    project_id: string;
    source_type: SourceType;
    source_url: string;
    language: string;
    command: string | null;
    target_nodes: string | null; // JSON-encoded list
    created_at: string;
    updated_at: string;
    is_deleted?: boolean;
}

export interface SpiderCreatePayload {
    name: string;
    description?: string;
    project_id: string;
    source_type: SourceType;
    source_url: string;
    language?: string;
    command?: string;
    target_nodes?: string[];
}

export interface SpiderUpdatePayload {
    name?: string;
    description?: string;
    project_id?: string;
    language?: string;
    command?: string;
    target_nodes?: string[];
}

export interface SpiderRunRequest {
    target_nodes?: string[];
}

export interface SpiderRunResponse {
    task_id: string;
}

// re-export for convenience
export type { SpiderNode };

export type TaskStatus = 'pending' | 'running' | 'success' | 'failed' | 'timeout' | 'error' | 'idle';

export interface SpiderTaskItem {
    id: number;
    task_id: string;
    spider_id: number;
    spider_name: string;
    status: TaskStatus;
    node_id: string | null;
    command: string | null;
    error_detail: string | null;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    is_deleted?: boolean;
}

export interface TaskLogItem {
    id: number;
    task_id: string;
    content: string;
    created_at: string;
}

export interface SpiderStatusData {
    status: TaskStatus;
    task_id: string | null;
    started_at: string | null;
    finished_at: string | null;
    is_deleted?: boolean;
}
