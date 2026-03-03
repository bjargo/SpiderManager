import request from '@/utils/request';
import type { SpiderNode } from '@/types/node';
import type { ApiResponse } from '@/types/api';

/**
 * Fetch list of all nodes
 */
export const fetchNodeList = (): Promise<ApiResponse<SpiderNode[]>> => {
    return request.get('/nodes');
};

export const updateNodeConfig = (nodeId: string, data: Partial<SpiderNode>): Promise<ApiResponse<any>> => {
    return request.post(`/nodes/${nodeId}/config`, data);
};

export const uninstallNode = (nodeId: string): Promise<ApiResponse<any>> => {
    return request.post(`/nodes/${nodeId}/delete`);
};
