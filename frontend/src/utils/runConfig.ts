/**
 * 运行配置前端工具。
 *
 * 运行配置以「入口文件 + 参数数组」两个独立字段提交，前端不会把整条命令字符串
 * 发往服务端（避免被机房侧安全设备误判为恶意命令）。这里的解析仅在浏览器内
 * 完成一次便利性的拆分，服务端仍会重新做严格校验。
 */

export interface RunConfigDraft {
    entrypoint: string;
    args: string[];
}

/** 各运行环境的默认入口文件（仅用于表单预填，提交前用户可修改） */
export const DEFAULT_ENTRYPOINTS: Record<string, string> = {
    'python:3.11-slim': 'main.py',
    'nodejs:18-slim': 'index.js',
    'golang:1.21-alpine': 'main.go',
    'default': 'run.sh',
};

const LAUNCHERS = new Set([
    'python',
    'python3',
    'py',
    'node',
    'nodejs',
    'sh',
    'bash',
    '/bin/sh',
    '/bin/bash',
]);

/** Shell 语法与控制字符：出现即认为不能作为结构化的入口/参数提交 */
const FORBIDDEN_FRAGMENTS = [';', '|', '&&', '||', '>', '<', '`', '$(', '${', '\\'];

/** 是否包含换行、制表符等控制字符 */
function hasControlChar(text: string): boolean {
    for (const char of text) {
        const code = char.codePointAt(0) ?? 0;
        if (code < 0x20 || code === 0x7f) {
            return true;
        }
    }
    return false;
}

/** 按 Shell 词法拆分文本，支持单双引号包裹的含空格参数 */
function tokenize(text: string): string[] {
    const tokens: string[] = [];
    let current = '';
    let quote: string | null = null;
    let started = false;

    for (const char of text) {
        if (quote) {
            if (char === quote) {
                quote = null;
            } else {
                current += char;
            }
            continue;
        }
        if (char === '"' || char === "'") {
            quote = char;
            started = true;
            continue;
        }
        if (/\s/.test(char)) {
            if (started) {
                tokens.push(current);
                current = '';
                started = false;
            }
            continue;
        }
        current += char;
        started = true;
    }

    if (started) {
        tokens.push(current);
    }
    return tokens;
}

/**
 * 把用户粘贴的整条命令拆成入口文件与参数数组。
 *
 * @param text 例如 `python main.py --mode all`
 * @returns 拆分结果；无法安全拆分时返回 null
 */
export function splitCommandLine(text: string): RunConfigDraft | null {
    const trimmed = text.trim();
    if (!trimmed) {
        return null;
    }
    if (FORBIDDEN_FRAGMENTS.some(fragment => trimmed.includes(fragment))) {
        return null;
    }
    if (hasControlChar(trimmed)) {
        return null;
    }

    let tokens = tokenize(trimmed);
    if (tokens.length === 0) {
        return null;
    }

    if (LAUNCHERS.has(tokens[0])) {
        tokens = tokens.slice(1);
    } else if (tokens[0] === 'go' && tokens[1] === 'run') {
        tokens = tokens.slice(2);
    }
    if (tokens.length === 0) {
        return null;
    }

    const [entrypoint, ...args] = tokens;
    if (entrypoint.startsWith('-') || entrypoint.includes('..')) {
        return null;
    }
    return { entrypoint, args };
}

/** 去掉首尾空白与空行，保留参数内部空格 */
export function normalizeArgsDraft(args: string[]): string[] {
    return args.map(item => item.trim()).filter(Boolean);
}
