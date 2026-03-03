export interface CronTaskResponse {
    job_id: string;
    spider_id: number;
    spider_name: string;
    cron_expr: string;
    description: string | null;
    enabled: boolean;
    target_node_ids: string[] | null;
    next_run_time: string | null;
}

export interface CronTaskCreate {
    spider_id: number;
    cron_expr: string;
    description?: string | null;
    enabled?: boolean;
    target_node_ids?: string[] | null;
    timeout_seconds?: number;
}

export interface CronTaskUpdate {
    spider_id?: number;
    cron_expr?: string;
    description?: string | null;
    enabled?: boolean;
    target_node_ids?: string[] | null;
    timeout_seconds?: number;
}
