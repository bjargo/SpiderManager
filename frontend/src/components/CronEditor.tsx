import React, { useState, useMemo } from 'react';
import { Clock, ChevronDown } from 'lucide-react';
import './CronEditor.css';

interface Props {
    value: string;
    onChange: (val: string) => void;
}

// ── 预设 ──
const presets = [
    { label: '每分钟', value: '* * * * *', desc: '每分钟执行一次' },
    { label: '每5分钟', value: '*/5 * * * *', desc: '每隔5分钟执行' },
    { label: '每10分钟', value: '*/10 * * * *', desc: '每隔10分钟执行' },
    { label: '每30分钟', value: '*/30 * * * *', desc: '每隔30分钟执行' },
    { label: '每小时', value: '0 * * * *', desc: '每小时整点执行' },
    { label: '每2小时', value: '0 */2 * * *', desc: '每隔2小时执行' },
    { label: '每天午夜', value: '0 0 * * *', desc: '每天 00:00 执行' },
    { label: '每天8点', value: '0 8 * * *', desc: '每天 08:00 执行' },
    { label: '每周一', value: '0 0 * * 1', desc: '每周一 00:00 执行' },
    { label: '每月1号', value: '0 0 1 * *', desc: '每月1号 00:00 执行' },
];

// ── 字段配置 ──
interface FieldConfig {
    label: string;
    short: string;
    options: { label: string; value: string }[];
}

const FIELD_CONFIGS: FieldConfig[] = [
    {
        label: '分钟', short: '分',
        options: [
            { label: '每分钟', value: '*' },
            { label: '每5分钟', value: '*/5' },
            { label: '每10分钟', value: '*/10' },
            { label: '每15分钟', value: '*/15' },
            { label: '每30分钟', value: '*/30' },
            { label: '第0分', value: '0' },
            { label: '第5分', value: '5' },
            { label: '第10分', value: '10' },
            { label: '第15分', value: '15' },
            { label: '第20分', value: '20' },
            { label: '第30分', value: '30' },
            { label: '第45分', value: '45' },
        ],
    },
    {
        label: '小时', short: '时',
        options: [
            { label: '每小时', value: '*' },
            { label: '每2小时', value: '*/2' },
            { label: '每3小时', value: '*/3' },
            { label: '每6小时', value: '*/6' },
            { label: '每12小时', value: '*/12' },
            { label: '0点', value: '0' },
            { label: '6点', value: '6' },
            { label: '8点', value: '8' },
            { label: '9点', value: '9' },
            { label: '12点', value: '12' },
            { label: '18点', value: '18' },
            { label: '22点', value: '22' },
        ],
    },
    {
        label: '日期', short: '日',
        options: [
            { label: '每天', value: '*' },
            { label: '1号', value: '1' },
            { label: '15号', value: '15' },
            { label: '每5天', value: '*/5' },
            { label: '每10天', value: '*/10' },
        ],
    },
    {
        label: '月份', short: '月',
        options: [
            { label: '每月', value: '*' },
            { label: '1月', value: '1' },
            { label: '每季度', value: '1,4,7,10' },
            { label: '每半年', value: '1,7' },
        ],
    },
    {
        label: '星期', short: '周',
        options: [
            { label: '不限', value: '*' },
            { label: '周一', value: '1' },
            { label: '周二', value: '2' },
            { label: '周三', value: '3' },
            { label: '周四', value: '4' },
            { label: '周五', value: '5' },
            { label: '周六', value: '6' },
            { label: '周日', value: '0' },
            { label: '工作日', value: '1-5' },
            { label: '周末', value: '0,6' },
        ],
    },
];

// ── 翻译函数：Cron → 自然语言 ──
function describeCron(expr: string): string {
    const parts = expr.trim().split(/\s+/);
    if (parts.length !== 5) return '无效表达式';

    const [minute, hour, day, month, weekday] = parts;
    const pieces: string[] = [];

    // 月
    if (month !== '*') {
        if (month.includes(',')) pieces.push(`${month.replace(/,/g, '、')} 月`);
        else if (month.includes('/')) pieces.push(`每 ${month.split('/')[1]} 个月`);
        else pieces.push(`${month} 月`);
    }

    // 星期
    const weekMap: Record<string, string> = { '0': '日', '1': '一', '2': '二', '3': '三', '4': '四', '5': '五', '6': '六' };
    if (weekday !== '*') {
        if (weekday === '1-5') pieces.push('工作日');
        else if (weekday === '0,6' || weekday === '6,0') pieces.push('周末');
        else {
            const wds = weekday.split(',').map(w => `周${weekMap[w] ?? w}`).join('、');
            pieces.push(wds);
        }
    }

    // 日
    if (day !== '*') {
        if (day.includes('/')) pieces.push(`每 ${day.split('/')[1]} 天`);
        else pieces.push(`${day} 号`);
    }

    // 时
    if (hour === '*') {
        pieces.push('每小时');
    } else if (hour.includes('/')) {
        pieces.push(`每 ${hour.split('/')[1]} 小时`);
    } else {
        const hh = hour.padStart(2, '0');
        if (minute === '*') {
            pieces.push(`${hh} 点每分钟`);
        } else if (minute.includes('/')) {
            pieces.push(`${hh} 点起每 ${minute.split('/')[1]} 分钟`);
        } else {
            pieces.push(`${hh}:${minute.padStart(2, '0')}`);
        }
        return pieces.join(' ') + ' 执行';
    }

    // 分（小时为 * 或 */n 时）
    if (minute === '*') {
        // already covered by "每小时" or "每 n 小时"
    } else if (minute.includes('/')) {
        pieces.push(`每 ${minute.split('/')[1]} 分钟`);
    } else {
        pieces.push(`第 ${minute} 分`);
    }

    return pieces.join(' ') + ' 执行';
}

// ── 获取字段的显示标签 ──
function getFieldLabel(fieldIndex: number, fieldValue: string): string {
    const config = FIELD_CONFIGS[fieldIndex];
    const match = config.options.find(o => o.value === fieldValue);
    return match ? match.label : fieldValue;
}

// ── FieldSelector 字段选择器 ──
interface FieldSelectorProps {
    config: FieldConfig;
    value: string;
    onChange: (val: string) => void;
    fieldIndex: number;
}

function FieldSelector({ config, value, onChange, fieldIndex }: FieldSelectorProps) {
    const [open, setOpen] = useState(false);
    const displayLabel = getFieldLabel(fieldIndex, value);
    const isDefault = value === '*';

    return (
        <div className={`ce-field ${open ? 'open' : ''}`}>
            <button
                type="button"
                className={`ce-field-trigger ${isDefault ? '' : 'active'}`}
                onClick={() => setOpen(!open)}
            >
                <span className="ce-field-label">{config.short}</span>
                <span className="ce-field-value">{displayLabel}</span>
                <ChevronDown size={12} className={`ce-field-arrow ${open ? 'up' : ''}`} />
            </button>

            {open && (
                <div className="ce-field-dropdown">
                    <div className="ce-field-dropdown-title">{config.label}</div>
                    <div className="ce-field-options">
                        {config.options.map(opt => (
                            <button
                                key={opt.value}
                                type="button"
                                className={`ce-field-option ${value === opt.value ? 'selected' : ''}`}
                                onClick={() => { onChange(opt.value); setOpen(false); }}
                            >
                                {opt.label}
                            </button>
                        ))}
                    </div>
                    <div className="ce-field-custom">
                        <input
                            type="text"
                            className="ce-field-custom-input"
                            placeholder="自定义"
                            defaultValue={config.options.some(o => o.value === value) ? '' : value}
                            onKeyDown={e => {
                                if (e.key === 'Enter') {
                                    const v = (e.target as HTMLInputElement).value.trim();
                                    if (v) { onChange(v); setOpen(false); }
                                }
                            }}
                        />
                    </div>
                </div>
            )}
        </div>
    );
}

// ── 主组件 ──
export const CronEditor: React.FC<Props> = ({ value, onChange }) => {
    const parts = value.trim().split(/\s+/);
    const fields = parts.length === 5 ? parts : ['*', '*', '*', '*', '*'];

    const updateField = (index: number, val: string) => {
        const newFields = [...fields];
        newFields[index] = val;
        onChange(newFields.join(' '));
    };

    const description = useMemo(() => describeCron(value), [value]);

    return (
        <div className="cron-editor-v2">
            {/* 预设快选 */}
            <div className="cev2-presets">
                {presets.map(p => (
                    <button
                        key={p.value}
                        type="button"
                        className={`cev2-preset ${value === p.value ? 'active' : ''}`}
                        onClick={() => onChange(p.value)}
                        title={p.desc}
                    >
                        {p.label}
                    </button>
                ))}
            </div>

            {/* 可视化字段编辑 */}
            <div className="cev2-fields">
                {FIELD_CONFIGS.map((cfg, i) => (
                    <FieldSelector
                        key={i}
                        config={cfg}
                        value={fields[i]}
                        onChange={val => updateField(i, val)}
                        fieldIndex={i}
                    />
                ))}
            </div>

            {/* 底部：原始输入 + 自然语描述 */}
            <div className="cev2-bottom">
                <div className="cev2-raw">
                    <span className="cev2-raw-label">CRON</span>
                    <input
                        type="text"
                        value={value}
                        onChange={e => onChange(e.target.value)}
                        placeholder="* * * * *"
                        className="cev2-raw-input"
                    />
                </div>
                <div className="cev2-desc">
                    <Clock size={13} />
                    <span>{description}</span>
                </div>
            </div>
        </div>
    );
};
