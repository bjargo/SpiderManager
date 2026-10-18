import { useEffect, useState } from 'react';

export type ThemeMode = 'dark' | 'light';

/** 读取当前主题（由 Layout 切换的 html.light-theme 类名决定） */
function readThemeMode(): ThemeMode {
    if (typeof document === 'undefined') return 'dark';
    return document.documentElement.classList.contains('light-theme') ? 'light' : 'dark';
}

/**
 * 监听全局主题切换。

 * 需要"按主题换色"的组件（ECharts 图表、CodeMirror 编辑器等）用它拿到当前模式，
 * 避免在 canvas 上直接使用 var(...)（canvas 不解析 CSS 变量）。
 */
export function useThemeMode(): ThemeMode {
    const [mode, setMode] = useState<ThemeMode>(readThemeMode);

    useEffect(() => {
        const observer = new MutationObserver(() => setMode(readThemeMode()));
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
        setMode(readThemeMode());
        return () => observer.disconnect();
    }, []);

    return mode;
}
