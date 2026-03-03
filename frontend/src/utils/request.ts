import axios, { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse } from 'axios';

const request: AxiosInstance = axios.create({
    baseURL: '/api',
    timeout: 10000,
});

request.interceptors.request.use(
    (config: InternalAxiosRequestConfig) => {
        const token = localStorage.getItem('token');
        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error: any) => {
        return Promise.reject(error);
    }
);

request.interceptors.response.use(
    (response: AxiosResponse) => {
        // If response is a file (blob/arraybuffer), return it directly
        if (response.config.responseType === 'blob' || response.config.responseType === 'arraybuffer') {
            return response.data;
        }

        // Wrap backend direct returns (since backend uses arbitrary returns, or specific schemas)
        // Adjust based on the actual backend response format. Assuming direct data return or {code, data, message}
        const res = response.data;
        return res;
    },
    (error: any) => {
        let message = '网络请求失败';
        if (error.response) {
            const status = error.response.status;
            switch (status) {
                case 400:
                    message = '请求参数错误';
                    break;
                case 401:
                    message = '暂无权限，请登录';
                    localStorage.removeItem('token');
                    window.location.href = '/login';
                    break;
                case 403:
                    message = '拒绝访问';
                    break;
                case 404:
                    message = '请求接口不存在';
                    break;
                case 500:
                    message = '服务器内部错误';
                    break;
                default:
                    message = `请求错误 (${status})`;
            }
            // You can implement UI Toast/Message notification here for fallback
            console.error(message, error.response.data);
        } else if (error.message.includes('timeout')) {
            message = '请求超时，请稍后重试';
            console.error(message);
        } else {
            console.error(message, error);
        }

        return Promise.reject(error);
    }
);

export default request;
