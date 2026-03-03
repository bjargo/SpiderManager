import request from '@/utils/request';
import type { CronTaskCreate, CronTaskResponse, CronTaskUpdate } from '@/types/scheduler';

export const fetchCronTasks = (): Promise<CronTaskResponse[]> => {
    return request.get('/tasks/cron');
};

export const addCronTask = (data: CronTaskCreate): Promise<CronTaskResponse> => {
    return request.post('/tasks/cron', data);
};

export const deleteCronTask = (jobId: string): Promise<{ message: string; job_id: string }> => {
    return request.post(`/tasks/cron/${jobId}/delete`);
};

export const updateCronTask = (jobId: string, data: CronTaskUpdate): Promise<CronTaskResponse> => {
    return request.post(`/tasks/cron/${jobId}/update`, data);
};

export const toggleCronTask = (jobId: string, enabled: boolean): Promise<CronTaskResponse> => {
    return request.post(`/tasks/cron/${jobId}/toggle`, { enabled });
};
