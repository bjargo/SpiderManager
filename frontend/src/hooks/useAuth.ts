/**
 * useAuth — 当前用户信息 Hook
 *
 * 在 token 存在时自动调用 /users/me 拉取用户信息（含 role），
 * 结果存入模块级缓存，避免同一页面内重复请求。
 *
 * 用法：
 *   const { user, role, isAdmin, isDeveloper, loading } = useAuth();
 */
import { useState, useEffect, useRef } from 'react';
import { getCurrentUser } from '../api/admin';
import type { CurrentUser, UserRole } from '../types/user';

// ── 模块级缓存，页面刷新前复用 ──
let _cache: CurrentUser | null = null;
let _promise: Promise<CurrentUser | null> | null = null;

async function fetchUser(): Promise<CurrentUser | null> {
    const token = localStorage.getItem('token');
    if (!token) return null;
    if (_cache) return _cache;
    if (_promise) return _promise;

    _promise = getCurrentUser()
        .then((res: any) => {
            // /users/me 是 fastapi-users 内置接口，直接返回 UserRead 对象，
            // 没有 { data: ... } 外层包装，因此需要兼容两种格式：
            //   - 有包装: { data: { id, email, role, ... } }
            //   - 无包装: { id, email, role, ... }
            const userData: CurrentUser | null = res?.data?.id
                ? res.data
                : res?.id
                    ? res
                    : null;
            if (userData) {
                _cache = userData;
                return _cache;
            }
            return null;
        })
        .catch(() => null)
        .finally(() => { _promise = null; });

    return _promise;
}

/** 主动清除缓存（登出时调用） */
export function clearAuthCache() {
    _cache = null;
    _promise = null;
}

export interface AuthState {
    user: CurrentUser | null;
    role: UserRole | null;
    isAdmin: boolean;
    isDeveloper: boolean;
    isViewer: boolean;
    loading: boolean;
}

export function useAuth(): AuthState {
    const [user, setUser] = useState<CurrentUser | null>(_cache);
    const [loading, setLoading] = useState<boolean>(!_cache);
    const mounted = useRef(true);

    useEffect(() => {
        mounted.current = true;
        if (_cache) {
            setUser(_cache);
            setLoading(false);
            return;
        }
        fetchUser().then(u => {
            if (mounted.current) {
                setUser(u);
                setLoading(false);
            }
        });
        return () => { mounted.current = false; };
    }, []);

    const role = user?.role ?? null;

    return {
        user,
        role,
        isAdmin: role === 'admin' || (user?.is_superuser ?? false),
        isDeveloper: role === 'developer' || role === 'admin' || (user?.is_superuser ?? false),
        isViewer: !!role,  // 任意角色均可查看
        loading,
    };
}
