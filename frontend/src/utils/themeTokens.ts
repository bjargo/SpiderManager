/**
 * 主题颜色工具

 * CSS 变量只能被浏览器样式系统解析，canvas 绘图（ECharts）与第三方组件
 * 需要拿到"具体颜色值"。这里统一通过 getComputedStyle 读取 CSS 变量的最终取值，
 * 保证图表/编辑器与界面使用同一套令牌，亮暗模式不会出现两套配色。
 */
import { useEffect, useState } from 'react';

import { useThemeMode } from '@/hooks/useThemeMode';

export type ThemeTokenMap = Record<string, string>;

/** 读取指定 CSS 变量在当前主题下的实际取值 */
export function resolveThemeTokens(names: readonly string[]): ThemeTokenMap {
    if (typeof window === 'undefined') {
        return Object.fromEntries(names.map(name => [name, '']));
    }
    const styles = getComputedStyle(document.documentElement);
    return Object.fromEntries(names.map(name => [name, styles.getPropertyValue(name).trim()]));
}

/**
 * 按主题自动刷新的颜色令牌 Hook。

 * 主题切换时重新解析 CSS 变量，供需要具体色值的组件（图表等）使用。
 */
export function useThemeTokens(names: readonly string[]): ThemeTokenMap {
    const mode = useThemeMode();
    const key = names.join(',');
    const [tokens, setTokens] = useState<ThemeTokenMap>(() => resolveThemeTokens(names));

    useEffect(() => {
        setTokens(resolveThemeTokens(key.split(',')));
    }, [key, mode]);

    return tokens;
}
