import request from '../utils/request';
import type { ApiResponse } from '../types/api';
import type { CurrentUser } from '../types/user';

/** 获取当前登录用户信息（含 role） */
export const getCurrentUser = (): Promise<ApiResponse<CurrentUser>> =>
    request({ url: '/users/me', method: 'get' });

/** 管理员创建用户 */
export interface AdminCreateUserPayload {
    email: string;
    role?: 'admin' | 'developer' | 'viewer';
    is_verified?: boolean;
}

export interface AdminCreateUserResult {
    id: string;
    email: string;
    role: string;
    initial_password: string;
}

export const adminCreateUser = (
    data: AdminCreateUserPayload
): Promise<ApiResponse<AdminCreateUserResult>> =>
    request({ url: '/admin/users', method: 'post', data });

/** 管理员禁用/启用用户 */
export const adminSetUserStatus = (
    userId: string,
    isActive: boolean
): Promise<ApiResponse<{ user_id: string; is_active: boolean }>> =>
    request({
        url: `/admin/users/${userId}/status`,
        method: 'post',
        data: { is_active: isActive },
    });

/** 审计日志 */
export interface AuditLogItem {
    id: number;
    operator_id: string;
    role: string;
    action: string;
    resource_type: string;
    resource_id: string;
    original_value: string | null;
    new_value: string | null;
    ip_address: string | null;
    status_code: number;
    created_at: string;
}

export const fetchAuditLogs = (params?: {
    operator_id?: string;
    action?: string;
    resource_type?: string;
    start_time?: string;
    end_time?: string;
    skip?: number;
    limit?: number;
}): Promise<ApiResponse<AuditLogItem[]>> =>
    request({ url: '/admin/logs', method: 'get', params });

/** 导出审计日志为 CSV */
export const exportAuditLogs = async (params?: {
    operator_id?: string;
    action?: string;
    resource_type?: string;
    start_time?: string;
    end_time?: string;
}): Promise<void> => {
    const queryParams = new URLSearchParams();
    if (params) {
        Object.entries(params).forEach(([k, v]) => {
            if (v) queryParams.append(k, v);
        });
    }
    const token = localStorage.getItem('token');
    const res = await fetch(`/api/admin/logs/export?${queryParams.toString()}`, {
        headers: {
            'Authorization': token ? `Bearer ${token}` : ''
        }
    });
    if (!res.ok) {
        throw new Error('导出失败');
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;

    // 获取后端返回的 filename（如果存在）
    const disposition = res.headers.get('content-disposition');
    let filename = `audit_logs_${new Date().getTime()}.csv`;
    if (disposition && disposition.indexOf('filename*=utf-8\'\'') !== -1) {
        const [, encoded] = disposition.split('filename*=utf-8\'\'');
        if (encoded) filename = decodeURIComponent(encoded);
    }

    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
};

