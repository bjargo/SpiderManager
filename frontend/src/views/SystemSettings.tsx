import { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Settings, Mail, CalendarClock, Save, Send, Eye, RefreshCw, Loader2,
    ShieldCheck, AlertTriangle, CheckCircle2, Info, Server, KeyRound, Clock,
} from 'lucide-react';
import {
    fetchDailyReportPreviewHtml,
    fetchReportSettings,
    saveReportSettings,
    sendDailyReportNow,
    sendTestMail,
} from '@/api/report';
import type { ReportSettings, ReportSettingsPayload } from '@/types/report';
import { useAuth } from '@/hooks/useAuth';
import './SystemSettings.css';

/** 页面内的提示条 */
interface Notice {
    type: 'info' | 'success' | 'warning' | 'danger';
    text: string;
}

/** 表单状态：字符串字段用 string 保存，避免数字输入框出现 NaN */
interface FormState {
    enabled: boolean;
    hour: number;
    minute: number;
    recipients: string;
    include_empty: boolean;
    smtp_host: string;
    smtp_port: string;
    smtp_user: string;
    smtp_password: string;
    smtp_from: string;
    encryption: 'ssl' | 'starttls' | 'none';
    smtp_timeout_seconds: string;
    smtp_max_retries: string;
}

const HOURS = Array.from({ length: 24 }, (_, i) => i);
const MINUTES = Array.from({ length: 60 }, (_, i) => i);

const NOTICE_ICON: Record<Notice['type'], JSX.Element> = {
    info: <Info size={15} />,
    success: <CheckCircle2 size={15} />,
    warning: <AlertTriangle size={15} />,
    danger: <AlertTriangle size={15} />,
};

function settingsToForm(data: ReportSettings): FormState {
    return {
        enabled: data.enabled,
        hour: data.hour,
        minute: data.minute,
        recipients: data.recipients,
        include_empty: data.include_empty,
        smtp_host: data.smtp_host,
        smtp_port: String(data.smtp_port),
        smtp_user: data.smtp_user,
        smtp_password: '',
        smtp_from: data.smtp_from,
        encryption: data.smtp_use_ssl ? 'ssl' : data.smtp_start_tls ? 'starttls' : 'none',
        smtp_timeout_seconds: String(data.smtp_timeout_seconds),
        smtp_max_retries: String(data.smtp_max_retries),
    };
}

function formToPayload(form: FormState): ReportSettingsPayload {
    const port = Number.parseInt(form.smtp_port, 10);
    const timeout = Number.parseInt(form.smtp_timeout_seconds, 10);
    const retries = Number.parseInt(form.smtp_max_retries, 10);

    return {
        enabled: form.enabled,
        hour: form.hour,
        minute: form.minute,
        recipients: form.recipients,
        include_empty: form.include_empty,
        smtp_host: form.smtp_host.trim(),
        smtp_port: Number.isFinite(port) ? port : 465,
        smtp_user: form.smtp_user.trim(),
        // 留空表示保持原口令不变
        smtp_password: form.smtp_password ? form.smtp_password : null,
        smtp_from: form.smtp_from.trim(),
        smtp_use_ssl: form.encryption === 'ssl',
        smtp_start_tls: form.encryption === 'starttls',
        smtp_timeout_seconds: Number.isFinite(timeout) ? timeout : 15,
        smtp_max_retries: Number.isFinite(retries) ? retries : 3,
    };
}

function extractErrorMessage(err: unknown): string {
    const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (err instanceof Error) return err.message;
    return '请求失败，请稍后重试';
}

function passwordFromLabel(source: ReportSettings['smtp_password_from']): string {
    if (source === 'database') return '已保存到数据库';
    if (source === 'environment') return '来自环境变量 .env';
    return '未配置';
}

export default function SystemSettings() {
    const { isAdmin, loading: authLoading } = useAuth();

    const [settings, setSettings] = useState<ReportSettings | null>(null);
    const [form, setForm] = useState<FormState | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);
    const [sending, setSending] = useState(false);
    const [previewing, setPreviewing] = useState(false);
    const [notice, setNotice] = useState<Notice | null>(null);
    const [actionDate, setActionDate] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetchReportSettings();
            setSettings(res.data);
            setForm(settingsToForm(res.data));
        } catch (err) {
            setNotice({ type: 'danger', text: `读取配置失败：${extractErrorMessage(err)}` });
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (isAdmin) {
            void load();
        } else if (!authLoading) {
            setLoading(false);
        }
    }, [isAdmin, authLoading, load]);

    const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
        setForm(prev => (prev ? { ...prev, [key]: value } : prev));
    };

    const dirty = useMemo(() => {
        if (!settings || !form) return false;
        return JSON.stringify(settingsToForm(settings)) !== JSON.stringify(form);
    }, [settings, form]);

    const handleSave = async () => {
        if (!form) return;

        const recipientCount = form.recipients
            .split(/[,;\n]/)
            .map(item => item.trim())
            .filter(Boolean).length;

        if (form.enabled && recipientCount === 0) {
            setNotice({ type: 'warning', text: '启用日报前请先填写至少一个收件人邮箱。' });
            return;
        }
        if (form.enabled && !form.smtp_host.trim()) {
            setNotice({ type: 'warning', text: '启用日报前请先填写 SMTP 服务器地址。' });
            return;
        }

        setSaving(true);
        setNotice(null);
        try {
            const res = await saveReportSettings(formToPayload(form));
            setSettings(res.data);
            setForm(settingsToForm(res.data));
            setNotice({ type: 'success', text: `${res.message}。${res.data.schedule_hint}` });
        } catch (err) {
            setNotice({ type: 'danger', text: `保存失败：${extractErrorMessage(err)}` });
        } finally {
            setSaving(false);
        }
    };

    const handleTestMail = async () => {
        setTesting(true);
        setNotice(null);
        try {
            const res = await sendTestMail();
            setNotice({ type: 'success', text: `测试邮件已投递至 ${res.data.recipients.join('、')}，请查收（含垃圾箱）。` });
        } catch (err) {
            setNotice({ type: 'danger', text: `测试邮件发送失败：${extractErrorMessage(err)}` });
        } finally {
            setTesting(false);
        }
    };

    const handleSendNow = async () => {
        setSending(true);
        setNotice(null);
        try {
            const res = await sendDailyReportNow(actionDate || undefined);
            const d = res.data;
            setNotice({
                type: 'success',
                text: `日报（${d.report_date}）已发送至 ${d.recipients.join('、')}；任务 ${d.total_tasks} 个，成功 ${d.success_count}，失败 ${d.failed_count}，采集 ${d.total_records} 条。`,
            });
        } catch (err) {
            setNotice({ type: 'danger', text: `日报发送失败：${extractErrorMessage(err)}` });
        } finally {
            setSending(false);
        }
    };

    const handlePreview = async () => {
        setPreviewing(true);
        setNotice(null);
        try {
            const html = await fetchDailyReportPreviewHtml(actionDate || undefined);
            const win = window.open('', '_blank');
            if (!win) {
                setNotice({ type: 'warning', text: '浏览器拦截了新窗口，请允许本站点弹出窗口后重试。' });
                return;
            }
            win.document.write(html);
            win.document.close();
        } catch (err) {
            setNotice({ type: 'danger', text: `预览失败：${extractErrorMessage(err)}` });
        } finally {
            setPreviewing(false);
        }
    };

    if (authLoading || loading) {
        return (
            <div className="ss-loading">
                <Loader2 size={18} className="ss-spin" />
                <span>正在加载系统设置…</span>
            </div>
        );
    }

    if (!isAdmin) {
        return (
            <div className="ss-page">
                <div className="ss-alert warning">
                    <AlertTriangle size={15} />
                    <div className="ss-alert-body">
                        仅 <strong>管理员（admin）</strong> 可查看与修改系统设置。
                    </div>
                </div>
            </div>
        );
    }

    if (!form || !settings) {
        return (
            <div className="ss-page">
                <div className="ss-alert danger">
                    <AlertTriangle size={15} />
                    <div className="ss-alert-body">配置加载失败，请点击右上角「重新加载」重试。</div>
                </div>
                <div className="ss-footer">
                    <button className="ss-btn" onClick={() => void load()}>
                        <RefreshCw size={15} /> 重新加载
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="ss-page">
            <div className="ss-header">
                <div>
                    <h1 className="ss-title">
                        <Settings size={20} /> 系统设置
                    </h1>
                    <p className="ss-subtitle">
                        配置每日任务统计日报的邮件通道与发送策略。保存后立即生效，无需重启服务。
                    </p>
                </div>
                <div className="ss-header-actions">
                    <button className="ss-btn ghost" onClick={() => void load()} disabled={saving}>
                        <RefreshCw size={15} /> 重新加载
                    </button>
                </div>
            </div>

            {/* 运行状态总览 */}
            <div className="ss-status-grid">
                <div className="ss-status-card">
                    <span className="ss-status-label">配置来源</span>
                    <span className="ss-status-value">
                        {settings.source === 'database' ? (
                            <><ShieldCheck size={14} /> 设置页面（数据库）</>
                        ) : (
                            <><Server size={14} /> 环境变量 .env</>
                        )}
                    </span>
                    <span className="ss-status-hint">
                        {settings.source === 'database'
                            ? '已保存到数据库，数据库为唯一事实源'
                            : '尚未在页面保存过，当前沿用 .env / docker-compose 中的值'}
                    </span>
                </div>

                <div className="ss-status-card">
                    <span className="ss-status-label">邮件通道</span>
                    <span className="ss-status-value">
                        {settings.smtp_ready
                            ? <span className="ss-badge ok">可用</span>
                            : <span className="ss-badge danger">未就绪</span>}
                        {settings.smtp_host && (
                            <span className="ss-status-hint">{settings.smtp_host}:{settings.smtp_port}</span>
                        )}
                    </span>
                    <span className="ss-status-hint">
                        授权码：{passwordFromLabel(settings.smtp_password_from)}
                    </span>
                </div>

                <div className="ss-status-card">
                    <span className="ss-status-label">自动发送</span>
                    <span className="ss-status-value">
                        {settings.enabled
                            ? <span className="ss-badge ok">已启用</span>
                            : <span className="ss-badge neutral">已停用</span>}
                        {settings.scheduler_active
                            ? <span className="ss-badge ok">本节点调度中</span>
                            : <span className="ss-badge warn">非 Leader 节点</span>}
                    </span>
                    <span className="ss-status-hint">{settings.schedule_hint}</span>
                </div>

                <div className="ss-status-card">
                    <span className="ss-status-label">下次发送</span>
                    <span className="ss-status-value">
                        <Clock size={14} />
                        {settings.next_run_time ?? '—'}
                    </span>
                    <span className="ss-status-hint">
                        按应用时区（Asia/Shanghai）统计「前一天」的任务
                    </span>
                </div>
            </div>

            {notice && (
                <div className={`ss-alert ${notice.type}`}>
                    {NOTICE_ICON[notice.type]}
                    <div className="ss-alert-body">{notice.text}</div>
                </div>
            )}

            {/* 邮件通道 */}
            <div className="ss-card">
                <div className="ss-card-head">
                    <span className="ss-card-title"><Mail size={16} /> 邮件通道（SMTP）</span>
                    <span className="ss-card-desc">以 163 / QQ 邮箱为例：465 + SSL，口令填授权码</span>
                </div>
                <div className="ss-card-body">
                    <div className="ss-grid">
                        <div className="ss-field">
                            <label>SMTP 服务器 <span className="ss-required">*</span></label>
                            <input
                                className="ss-input"
                                value={form.smtp_host}
                                placeholder="smtp.163.com"
                                onChange={e => update('smtp_host', e.target.value)}
                            />
                        </div>

                        <div className="ss-field">
                            <label>端口与加密方式</label>
                            <div className="ss-inline">
                                <input
                                    className="ss-input"
                                    style={{ maxWidth: 110 }}
                                    value={form.smtp_port}
                                    placeholder="465"
                                    onChange={e => update('smtp_port', e.target.value.replace(/[^\d]/g, ''))}
                                />
                                <select
                                    className="ss-select"
                                    value={form.encryption}
                                    onChange={e => update('encryption', e.target.value as FormState['encryption'])}
                                >
                                    <option value="ssl">SSL（465，推荐）</option>
                                    <option value="starttls">STARTTLS（587）</option>
                                    <option value="none">不加密（内网中继）</option>
                                </select>
                            </div>
                        </div>

                        <div className="ss-field">
                            <label>认证账号</label>
                            <input
                                className="ss-input"
                                value={form.smtp_user}
                                placeholder="spiderbot@example.com"
                                onChange={e => update('smtp_user', e.target.value)}
                            />
                        </div>

                        <div className="ss-field">
                            <label>
                                <KeyRound size={13} /> 授权码 / 密码
                                <span className="ss-badge neutral">{passwordFromLabel(settings.smtp_password_from)}</span>
                            </label>
                            <input
                                className="ss-input"
                                type="password"
                                autoComplete="new-password"
                                value={form.smtp_password}
                                placeholder={settings.smtp_password_set ? '已配置，留空表示不修改' : '请输入邮箱授权码'}
                                onChange={e => update('smtp_password', e.target.value)}
                            />
                            <span className="ss-help">授权码不是登录密码；请勿泄露，泄露后请到邮箱后台重新生成。</span>
                        </div>

                        <div className="ss-field">
                            <label>发件人地址</label>
                            <input
                                className="ss-input"
                                value={form.smtp_from}
                                placeholder="留空则使用认证账号"
                                onChange={e => update('smtp_from', e.target.value)}
                            />
                            <span className="ss-help">多数邮箱要求发件人与认证账号一致，否则会被拒信。</span>
                        </div>

                        <div className="ss-field">
                            <label>超时 / 重试</label>
                            <div className="ss-inline">
                                <input
                                    className="ss-input"
                                    value={form.smtp_timeout_seconds}
                                    placeholder="15"
                                    onChange={e => update('smtp_timeout_seconds', e.target.value.replace(/[^\d]/g, ''))}
                                />
                                <span className="ss-inline-sep">秒 /</span>
                                <input
                                    className="ss-input"
                                    value={form.smtp_max_retries}
                                    placeholder="3"
                                    onChange={e => update('smtp_max_retries', e.target.value.replace(/[^\d]/g, ''))}
                                />
                                <span className="ss-inline-sep">次</span>
                            </div>
                        </div>
                    </div>

                    <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
                        <button className="ss-btn" onClick={() => void handleTestMail()} disabled={testing}>
                            {testing ? <Loader2 size={15} className="ss-spin" /> : <Send size={15} />} 发送测试邮件
                        </button>
                        <span className="ss-help" style={{ alignSelf: 'center' }}>
                            测试邮件使用<strong>已保存</strong>的配置，请先保存再测试。
                        </span>
                    </div>
                </div>
            </div>

            {/* 日报策略 */}
            <div className="ss-card">
                <div className="ss-card-head">
                    <span className="ss-card-title"><CalendarClock size={16} /> 日报策略</span>
                    <span className="ss-card-desc">统计前一日 00:00 ~ 24:00 的任务运行情况</span>
                </div>
                <div className="ss-card-body">
                    <div className="ss-grid">
                        <div className="ss-field full">
                            <div className="ss-switch-row">
                                <label className="ss-switch">
                                    <input
                                        type="checkbox"
                                        checked={form.enabled}
                                        onChange={e => update('enabled', e.target.checked)}
                                    />
                                    <span className="ss-switch-track" />
                                </label>
                                <div className="ss-switch-text">
                                    <span className="ss-switch-title">启用每日自动发送</span>
                                    <span className="ss-help">
                                        关闭后不会自动发送，但「立即发送」「预览」等功能仍可手动使用。
                                    </span>
                                </div>
                            </div>
                        </div>

                        <div className="ss-field">
                            <label>发送时刻（Asia/Shanghai）</label>
                            <div className="ss-inline">
                                <select
                                    className="ss-select"
                                    value={form.hour}
                                    onChange={e => update('hour', Number(e.target.value))}
                                >
                                    {HOURS.map(h => (
                                        <option key={h} value={h}>{String(h).padStart(2, '0')} 时</option>
                                    ))}
                                </select>
                                <span className="ss-inline-sep">:</span>
                                <select
                                    className="ss-select"
                                    value={form.minute}
                                    onChange={e => update('minute', Number(e.target.value))}
                                >
                                    {MINUTES.map(m => (
                                        <option key={m} value={m}>{String(m).padStart(2, '0')} 分</option>
                                    ))}
                                </select>
                            </div>
                        </div>

                        <div className="ss-field full">
                            <label>收件人 <span className="ss-required">*</span></label>
                            <textarea
                                className="ss-textarea"
                                value={form.recipients}
                                placeholder="ops@company.com, lead@company.com"
                                onChange={e => update('recipients', e.target.value)}
                            />
                            <span className="ss-help">
                                支持多个邮箱，用英文逗号、分号或换行分隔；已解析 {settings.recipient_list.length} 个地址。
                            </span>
                        </div>

                        <div className="ss-field full">
                            <label className="ss-inline" style={{ cursor: 'pointer' }}>
                                <input
                                    type="checkbox"
                                    checked={form.include_empty}
                                    onChange={e => update('include_empty', e.target.checked)}
                                />
                                <span>无任务时也发送简报</span>
                            </label>
                            <span className="ss-help">关闭后，当天没有任何任务时不发送邮件。</span>
                        </div>
                    </div>
                </div>
            </div>

            {/* 手动操作 */}
            <div className="ss-card">
                <div className="ss-card-head">
                    <span className="ss-card-title"><Eye size={16} /> 预览与手动补发</span>
                    <span className="ss-card-desc">用于联调与漏发补发，不影响自动发送计划</span>
                </div>
                <div className="ss-card-body">
                    <div className="ss-grid">
                        <div className="ss-field">
                            <label>操作日期</label>
                            <input
                                className="ss-input"
                                type="date"
                                value={actionDate}
                                onChange={e => setActionDate(e.target.value)}
                            />
                            <span className="ss-help">留空表示统计「昨天」。</span>
                        </div>
                        <div className="ss-field" style={{ justifyContent: 'flex-end' }}>
                            <div className="ss-inline">
                                <button className="ss-btn" onClick={() => void handlePreview()} disabled={previewing}>
                                    {previewing ? <Loader2 size={15} className="ss-spin" /> : <Eye size={15} />} 预览邮件
                                </button>
                                <button className="ss-btn" onClick={() => void handleSendNow()} disabled={sending}>
                                    {sending ? <Loader2 size={15} className="ss-spin" /> : <Send size={15} />} 立即发送
                                </button>
                            </div>
                        </div>
                    </div>

                    <div className="ss-meta" style={{ marginTop: 16 }}>
                        <span>最后修改：<b>{settings.updated_at ?? '—'}</b></span>
                        <span>修改人：<b>{settings.updated_by ?? '—'}</b></span>
                        <span>调度任务：<b>{settings.job_registered ? '已注册' : '未注册'}</b></span>
                    </div>
                </div>
            </div>

            <div className="ss-footer">
                <span className="ss-footer-note">
                    {dirty ? '存在未保存的修改' : '当前配置已与服务器一致'}
                </span>
                <button className="ss-btn ghost" onClick={() => setForm(settingsToForm(settings))} disabled={!dirty || saving}>
                    放弃修改
                </button>
                <button className="ss-btn primary" onClick={() => void handleSave()} disabled={saving || !dirty}>
                    {saving ? <Loader2 size={15} className="ss-spin" /> : <Save size={15} />} 保存配置
                </button>
            </div>
        </div>
    );
}
