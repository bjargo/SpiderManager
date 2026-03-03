import request from '../utils/request';

export interface LoginParams {
    username: string;
    password: string;
}

export interface LoginResponse {
    access_token: string;
    token_type: string;
}

export const login = (data: LoginParams): Promise<LoginResponse> => {
    const formData = new URLSearchParams();
    formData.append('username', data.username);
    formData.append('password', data.password);

    return request({
        url: '/users/login',
        method: 'post',
        data: formData,
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    });
};
