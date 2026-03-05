import request from '@/utils/request';
import type { ApiResponse } from '@/types/api';
import type { TaskRequest, TaskResponse, TaskListParams, TaskListResponse } from '@/types/task';

/**
 * 下发一次性爬虫任务
 */
export const runTask = (data: TaskRequest) => {
    return request.post<any, TaskResponse>('/tasks/run', data);
};

export const fetchTaskList = (params?: TaskListParams): Promise<ApiResponse<TaskListResponse>> => {
    return request.get('/tasks', { params });
};

export const stopTask = (taskId: string): Promise<ApiResponse<any>> => {
    return request.post(`/tasks/${taskId}/stop`);
};

export const deleteTask = (taskId: string): Promise<ApiResponse<any>> => {
    return request.post(`/tasks/${taskId}/delete`);
};

export const fetchTaskLogs = (taskId: string): Promise<ApiResponse<any>> => {
    return request.get(`/tasks/${taskId}/logs`);
};
