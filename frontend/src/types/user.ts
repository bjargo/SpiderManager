/**
 * 用户相关类型定义
 */
export type UserRole = 'admin' | 'developer' | 'viewer';

export interface CurrentUser {
    id: string;
    email: string;
    role: UserRole;
    is_active: boolean;
    is_verified: boolean;
    is_superuser: boolean;
}
