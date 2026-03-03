import request from '@/utils/request';
import type { ApiResponse } from '@/types/api';
import type {
    SpiderItem,
    SpiderCreatePayload,
    SpiderUpdatePayload,
    SpiderRunRequest,
    SpiderRunResponse,
    SpiderTaskItem,
    TaskLogItem,
    SpiderStatusData,
} from '@/types/spider';

/** 获取爬虫列表 */
export const fetchSpiderList = (): Promise<ApiResponse<SpiderItem[]>> => {
    return request.get('/spiders/');
};

/** 创建爬虫（通用，source_type 由调用方指定） */
export const createSpider = (data: SpiderCreatePayload): Promise<ApiResponse<SpiderItem>> => {
    return request.post('/spiders/', data);
};

/** 上传 ZIP 并获取 MinIO source_url */
export const uploadSpiderZip = (
    formData: FormData,
    onUploadProgress?: (event: any) => void,
): Promise<ApiResponse<{ source_url: string }>> => {
    return request.post('/spiders/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress,
    });
};

/** 更新爬虫元数据 */
export const updateSpider = (
    spiderId: number,
    data: SpiderUpdatePayload,
): Promise<ApiResponse<SpiderItem>> => {
    return request.post(`/spiders/${spiderId}/update`, data);
};

/** 删除爬虫 */
export const deleteSpider = (spiderId: number): Promise<ApiResponse<null>> => {
    return request.post(`/spiders/${spiderId}/delete`);
};

/** 触发爬虫运行（将任务推入 Redis 队列） */
export const runSpider = (
    spiderId: number,
    data: SpiderRunRequest,
): Promise<ApiResponse<SpiderRunResponse>> => {
    return request.post(`/spiders/${spiderId}/run`, data);
};

/** 列出爬虫 ZIP 包内的文件 */
export const fetchSpiderFiles = (
    spiderId: number,
): Promise<ApiResponse<string[]>> => {
    return request.get(`/spiders/${spiderId}/files`);
};

/** 读取爬虫 ZIP 包内指定文件的内容 */
export const fetchSpiderFileContent = (
    spiderId: number,
    path: string,
): Promise<ApiResponse<{ path: string; content: string }>> => {
    return request.get(`/spiders/${spiderId}/file`, { params: { path } });
};

/** 保存修改后的文件内容到爬虫 ZIP 包 */
export const saveSpiderFileContent = (
    spiderId: number,
    data: { path: string; content: string },
): Promise<ApiResponse<null>> => {
    return request.post(`/spiders/${spiderId}/file`, data);
};

/** 新增文件到爬虫 ZIP 包 */
export const createSpiderFile = (
    spiderId: number,
    data: { path: string; content?: string },
): Promise<ApiResponse<string[]>> => {
    return request.post(`/spiders/${spiderId}/file/create`, data);
};

/** 从爬虫 ZIP 包中删除文件 */
export const deleteSpiderFile = (
    spiderId: number,
    data: { path: string },
): Promise<ApiResponse<string[]>> => {
    return request.post(`/spiders/${spiderId}/file/delete`, data);
};

/** 获取爬虫的任务历史列表 */
export const fetchSpiderTasks = (
    spiderId: number,
): Promise<ApiResponse<SpiderTaskItem[]>> => {
    return request.get(`/spiders/${spiderId}/tasks`);
};

/** 获取指定任务的日志 */
export const fetchTaskLogs = (
    spiderId: number,
    taskId: string,
): Promise<ApiResponse<TaskLogItem[]>> => {
    return request.get(`/spiders/${spiderId}/tasks/${taskId}/logs`);
};

/** 获取爬虫最新任务状态 */
export const fetchSpiderStatus = (
    spiderId: number,
): Promise<ApiResponse<SpiderStatusData>> => {
    return request.get(`/spiders/${spiderId}/status`);
};
