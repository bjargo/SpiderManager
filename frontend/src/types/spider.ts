import type { SpiderNode } from './node';

export type SourceType = 'MINIO' | 'GIT';

/** 运行配置状态：unparsed 表示历史配置无法自动解析，需人工重新保存 */
export type RunConfigStatus = 'ok' | 'empty' | 'unparsed';

export interface SpiderItem {
    id: number;
    name: string;
    description: string | null;
    project_id: string;
    source_type: SourceType;
    source_url: string;
    language: string;
    /** 入口文件相对路径（不含解释器名称），例如 main.py */
    entrypoint: string | null;
    /** 运行参数数组，每一项对应一个独立命令行参数 */
    args: string[];
    run_config_status: RunConfigStatus;
    target_nodes: string[] | null;
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
    entrypoint?: string;
    args?: string[];
    target_nodes?: string[];
}

export interface SpiderUpdatePayload {
    name?: string;
    description?: string;
    project_id?: string;
    language?: string;
    entrypoint?: string;
    args?: string[];
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
    entrypoint: string | null;
    args: string[];
    run_config_status: RunConfigStatus;
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
