export interface TaskRequest {
    task_id: string;
    spider_id: number;
    /** 入口文件相对路径；省略时使用爬虫已保存的配置 */
    entrypoint?: string;
    /** 运行参数数组；省略时使用爬虫已保存的配置 */
    args?: string[];
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
    entrypoint: string | null;
    args: string[];
    run_config_status: 'ok' | 'empty' | 'unparsed';
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
