import request from '@/utils/request';
import type { ApiResponse } from '@/types/api';

export interface DashboardStats {
    onlineNodes: number;
    totalNodes: number;
    totalSpiders: number;
    tasksToday: number;
    failedTasksToday: number;
}

export interface TrendData {
    date: string;
    success: number;
    failure: number;
}

export interface RecentTask {
    id: string;
    spiderName: string;
    nodeName: string;
    status: 'success' | 'failed' | 'running';
    startTime: string;
    endTime?: string;
}

export const fetchDashboardStats = (): Promise<ApiResponse<DashboardStats>> => {
    return request.get<any, ApiResponse<DashboardStats>>('/dashboard/stats');
};

export const fetchTaskTrends = (): Promise<ApiResponse<TrendData[]>> => {
    return request.get<any, ApiResponse<TrendData[]>>('/dashboard/trends');
};

export const fetchRecentTasks = (): Promise<ApiResponse<RecentTask[]>> => {
    return request.get<any, ApiResponse<RecentTask[]>>('/dashboard/recent');
};
