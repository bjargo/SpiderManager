/**
 * 每日任务日报与邮件通道配置类型定义
 */

/** 配置来源：database = 设置页面已保存；environment = 环境变量 */
export type ConfigSource = 'database' | 'environment';

/** SMTP 口令来源 */
export type PasswordSource = 'database' | 'environment' | 'none';

export interface ReportSettings {
    source: ConfigSource;
    enabled: boolean;
    hour: number;
    minute: number;
    recipients: string;
    recipient_list: string[];
    include_empty: boolean;

    smtp_host: string;
    smtp_port: number;
    smtp_user: string;
    /** 是否已配置口令（口令本身永不回显） */
    smtp_password_set: boolean;
    smtp_password_from: PasswordSource;
    smtp_from: string;
    smtp_use_ssl: boolean;
    smtp_start_tls: boolean;
    smtp_timeout_seconds: number;
    smtp_max_retries: number;
    smtp_ready: boolean;

    updated_at: string | null;
    updated_by: string | null;

    scheduler_active: boolean;
    job_registered: boolean;
    next_run_time: string | null;
    schedule_hint: string;
}

/** 保存请求体；smtp_password 留空/省略表示保持原口令不变 */
export interface ReportSettingsPayload {
    enabled: boolean;
    hour: number;
    minute: number;
    recipients: string;
    include_empty: boolean;
    smtp_host: string;
    smtp_port: number;
    smtp_user: string;
    smtp_password?: string | null;
    smtp_from: string;
    smtp_use_ssl: boolean;
    smtp_start_tls: boolean;
    smtp_timeout_seconds: number;
    smtp_max_retries: number;
}

export interface TestMailResult {
    recipients: string[];
}

export interface SendDailyReportResult {
    report_date: string;
    recipients: string[];
    total_tasks: number;
    success_count: number;
    failed_count: number;
    total_records: number;
}
