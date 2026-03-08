import request from '@/utils/request';
import type { ApiResponse } from '@/types/api';

export interface ProjectItem {
    project_id: string;
    name: string;
    description: string | null;
    created_at: string;
    updated_at: string;
    spider_count: number;
    is_deleted?: boolean;
}

export const fetchProjectList = (): Promise<ApiResponse<ProjectItem[]>> => {
    return request.get('/projects');
};

export const createProject = (data: { name: string; description?: string }): Promise<ApiResponse<ProjectItem>> => {
    return request.post('/projects', data);
};

export const updateProject = (projectId: string, data: { name?: string; description?: string }): Promise<ApiResponse<ProjectItem>> => {
    return request.post(`/projects/${projectId}/update`, data);
};

export const deleteProject = (projectId: string): Promise<ApiResponse<null>> => {
    return request.post(`/projects/${projectId}/delete`);
};

export const uploadProjectZip = (data: FormData, onUploadProgress?: (progressEvent: any) => void): Promise<ApiResponse<ProjectItem>> => {
    return request.post('/projects/upload', data, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress,
    });
};

export const registerGitProject = (data: FormData): Promise<ApiResponse<ProjectItem>> => {
    return request.post('/projects/git', data);
};
