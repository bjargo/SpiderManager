/**
 * PermissionGuard — React 版本的 v-permission 指令
 *
 * 根据当前用户角色条件渲染子节点，角色不满足时渲染 fallback（默认 null）。
 *
 * 用法：
 *   // 仅 admin 可见
 *   <PermissionGuard roles={['admin']}>
 *     <DeleteButton />
 *   </PermissionGuard>
 *
 *   // admin + developer 可见，不满足时显示 fallback
 *   <PermissionGuard roles={['admin', 'developer']} fallback={<span>无权限</span>}>
 *     <EditButton />
 *   </PermissionGuard>
 */
import type { ReactNode } from 'react';
import { useAuth } from '../hooks/useAuth';
import type { UserRole } from '../types/user';

interface PermissionGuardProps {
    /** 允许显示内容的角色列表 */
    roles: UserRole[];
    /** 子节点 */
    children: ReactNode;
    /** 无权限时的 fallback，默认 null（直接隐藏） */
    fallback?: ReactNode;
}

export default function PermissionGuard({
    roles,
    children,
    fallback = null,
}: PermissionGuardProps) {
    const { role, user, loading } = useAuth();

    // 加载中时不渲染，避免闪烁
    if (loading) return null;

    // superuser 直接通过
    if (user?.is_superuser) return <>{children}</>;

    // 检查角色白名单
    if (role && roles.includes(role)) return <>{children}</>;

    return <>{fallback}</>;
}
