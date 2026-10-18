/**
 * 每日任务日报与邮件通道配置 API
 */
import request from '../utils/request';
import type { ApiResponse } from '../types/api';
import type {
    ReportSettings,
    ReportSettingsPayload,
    SendDailyReportResult,
    TestMailResult,
} from '../types/report';

/** 读取当前生效配置（数据库优先，回落环境变量） */
export const fetchReportSettings = (): Promise<ApiResponse<ReportSettings>> =>
    request({ url: '/reports/settings', method: 'get' });

/** 保存配置（保存后立即尝试对齐调度） */
export const saveReportSettings = (
    data: ReportSettingsPayload
): Promise<ApiResponse<ReportSettings>> =>
    request({ url: '/reports/settings', method: 'post', data });

/** 使用当前已保存的配置发送测试邮件；可临时指定收件人 */
export const sendTestMail = (
    recipients?: string[]
): Promise<ApiResponse<TestMailResult>> =>
    request({
        url: '/reports/settings/test-mail',
        method: 'post',
        data: { recipients: recipients && recipients.length ? recipients : null },
    });

/** 立即生成并发送指定日期的日报（不传日期则统计昨天） */
export const sendDailyReportNow = (
    date?: string,
    recipients?: string[]
): Promise<ApiResponse<SendDailyReportResult>> =>
    request({
        url: '/reports/daily/send',
        method: 'post',
        data: {
            date: date ?? null,
            recipients: recipients && recipients.length ? recipients : null,
        },
    });

/** 以文本形式获取日报邮件 HTML（用于在新窗口预览排版） */
export const fetchDailyReportPreviewHtml = (date?: string): Promise<string> =>
    request({
        url: '/reports/daily/preview-html',
        method: 'get',
        params: date ? { date } : undefined,
        responseType: 'text',
    });
