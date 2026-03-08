export interface TaskRequest {
    task_id: string;
    project_id: string;
    script_path: string;
    target_node_ids: string[] | null;
    timeout_seconds: number;
}

export interface TaskResponse {
    message: string;
    task_id: string;
    queues: string[];
}

export interface SpiderTaskOut {
    id: number;
    task_id: string;
    spider_id: number;
    spider_name: string;
    status: string;
    node_id: string | null;
    command: string | null;
    error_detail: string | null;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    is_deleted?: boolean;
}

export interface TaskListParams {
    skip?: number;
    limit?: number;
    status?: string;
    spider_id?: number;
    task_id?: string;
    start_time?: string;
    end_time?: string;
}

export interface TaskListResponse {
    items: SpiderTaskOut[];
    total: number;
}
