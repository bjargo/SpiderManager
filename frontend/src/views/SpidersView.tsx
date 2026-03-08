import { useState, useEffect, useCallback, useRef, DragEvent } from 'react';
import { useLocation } from 'react-router-dom';
import { Bug, Plus, Play, Pencil, Trash2, X, Copy, Check, UploadCloud, GitBranch, Server, Loader2, ChevronDown, Code, History } from 'lucide-react';
import CodeEditorModal from './CodeEditorModal';
import TaskHistoryModal from './TaskHistoryModal';
import DangerConfirmModal from '@/components/DangerConfirmModal';
import PermissionGuard from '@/components/PermissionGuard';
import {
    fetchSpiderList,
    createSpider,
    uploadSpiderZip,
    updateSpider,
    deleteSpider,
    runSpider,
} from '@/api/spider';
import { fetchNodeList } from '@/api/node';
import { fetchProjectList, type ProjectItem } from '@/api/project';
import type { SpiderItem, SpiderCreatePayload, SourceType } from '@/types/spider';
import type { SpiderNode } from '@/types/node';
import './SpidersView.css';

// ─────────────────────────────────────────────────
// 小工具：Toast 提示
// ─────────────────────────────────────────────────
type ToastType = 'success' | 'error' | 'info';
interface Toast { id: number; type: ToastType; msg: string }

function useToast() {
    const [toasts, setToasts] = useState<Toast[]>([]);
    const counter = useRef(0);

    const show = useCallback((msg: string, type: ToastType = 'success') => {
        const id = ++counter.current;
        setToasts(prev => [...prev, { id, type, msg }]);
        setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000);
    }, []);

    return { toasts, show };
}

// ─────────────────────────────────────────────────
// 来源 Tag
// ─────────────────────────────────────────────────
function SourceTag({ type }: { type: SourceType }) {
    if (type === 'MINIO') {
        return (
            <span className="sv-tag sv-tag-minio">
                <Server size={10} /> MinIO
            </span>
        );
    }
    return (
        <span className="sv-tag sv-tag-git">
            <GitBranch size={10} /> Git
        </span>
    );
}

// ─────────────────────────────────────────────────
// 可复制地址单元格
// ─────────────────────────────────────────────────
function CopyableUrl({ url }: { url: string }) {
    const [copied, setCopied] = useState(false);

    const handleCopy = (e: React.MouseEvent) => {
        e.stopPropagation();
        navigator.clipboard.writeText(url).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        });
    };

    return (
        <div className="sv-source-url">
            <span className="sv-url-text" title={url}>{url}</span>
            <button
                className={`sv-copy-btn ${copied ? 'copied' : ''}`}
                onClick={handleCopy}
                title="复制地址"
            >
                {copied ? <Check size={13} /> : <Copy size={13} />}
            </button>
        </div>
    );
}

// ─────────────────────────────────────────────────
// 格式化时间
// ─────────────────────────────────────────────────
function formatDateTime(iso: string): string {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// ─────────────────────────────────────────────────
// 新建/编辑 侧边抽屉
// ─────────────────────────────────────────────────
type DrawerMode = 'create' | 'edit';
type Tab = 'minio' | 'git';

interface DrawerProps {
    mode: DrawerMode;
    initial?: SpiderItem | null;
    onClose: () => void;
    onSaved: () => void;
    showToast: (msg: string, type?: ToastType) => void;
    projects: ProjectItem[];
}

// ─────────────────────────────────────────────────
// 自定义下拉选择组件
// ─────────────────────────────────────────────────
interface OptionItem {
    label: string;
    value: string;
}

interface CustomSelectProps {
    value: string;
    onChange: (val: string) => void;
    options: OptionItem[];
    placeholder?: string;
    style?: React.CSSProperties;
}

function CustomSelect({ value, onChange, options, placeholder, style }: CustomSelectProps) {
    const [isOpen, setIsOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);
    const selected = options.find(o => o.value === value);

    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    return (
        <div className="sv-custom-select" ref={containerRef} style={style}>
            <div
                className={`sv-custom-select-trigger ${isOpen ? 'open' : ''}`}
                onClick={() => setIsOpen(!isOpen)}
            >
                <span className="sv-custom-select-value">
                    {selected ? selected.label : (placeholder || '请选择')}
                </span>
                <ChevronDown size={15} className={`sv-custom-select-icon ${isOpen ? 'open' : ''}`} />
            </div>
            {isOpen && (
                <div className="sv-custom-select-dropdown">
                    {options.length === 0 ? (
                        <div className="sv-custom-select-empty">暂无选项</div>
                    ) : (
                        options.map(opt => (
                            <div
                                key={opt.value}
                                className={`sv-custom-select-option ${opt.value === value ? 'selected' : ''}`}
                                onClick={() => {
                                    onChange(opt.value);
                                    setIsOpen(false);
                                }}
                            >
                                {opt.label}
                            </div>
                        ))
                    )}
                </div>
            )}
        </div>
    );
}

function SpiderDrawer({ mode, initial, onClose, onSaved, showToast, projects }: DrawerProps) {
    const [tab, setTab] = useState<Tab>(
        mode === 'edit' ? (initial?.source_type === 'GIT' ? 'git' : 'minio') : 'minio'
    );

    // 公共字段
    const [name, setName] = useState(initial?.name ?? '');
    const [language, setLanguage] = useState(initial?.language ?? 'python:3.11-slim');
    const [command, setCommand] = useState(initial?.command ?? '');
    const [projectId, setProjectId] = useState(initial?.project_id ?? (projects.length > 0 ? projects[0].project_id : 'default'));

    // 预设语言选项及默认命令
    const languageOptions: OptionItem[] = [
        { label: 'Python 3.11', value: 'python:3.11-slim' },
        { label: 'Node.js 18', value: 'nodejs:18-slim' },
        { label: 'Golang 1.21', value: 'golang:1.21-alpine' },
        { label: '系统默认环境', value: 'default' },
    ];

    const defaultCommands: Record<string, string> = {
        'python:3.11-slim': 'main.py',
        'nodejs:18-slim': 'index.js',
        'golang:1.21-alpine': 'main.go',
        'default': 'run.sh',
    };

    // 语言改变时，如果当前 command 为空或者是之前语言的默认 command，则自动填充
    const handleLanguageChange = (newLang: string) => {
        const oldDefault = defaultCommands[language];
        const newDefault = defaultCommands[newLang] || '';

        if (!command.trim() || command.trim() === oldDefault) {
            setCommand(newDefault);
        }
        setLanguage(newLang);
    };

    // MinIO 字段
    const [file, setFile] = useState<File | null>(null);
    const [uploadProgress, setUploadProgress] = useState(0);
    const [uploading, setUploading] = useState(false);
    const [uploadedUrl, setUploadedUrl] = useState(
        mode === 'edit' && initial?.source_type === 'MINIO' ? initial.source_url : ''
    );
    const [dragOver, setDragOver] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Git 字段
    const [gitUrl, setGitUrl] = useState(() => {
        if (mode === 'edit' && initial?.source_type === 'GIT') {
            return initial.source_url.includes('@') ? initial.source_url.split('@')[0] : initial.source_url;
        }
        return '';
    });
    const [gitBranch, setGitBranch] = useState(() => {
        if (mode === 'edit' && initial?.source_type === 'GIT' && initial.source_url.includes('@')) {
            return initial.source_url.split('@')[1];
        }
        return 'main';
    });

    const [submitting, setSubmitting] = useState(false);

    // 文件选中处理
    const handleFileSelect = (f: File | null) => {
        if (!f) return;
        if (!f.name.endsWith('.zip')) {
            showToast('只支持 .zip 格式文件', 'error');
            return;
        }
        setFile(f);
        setUploadedUrl('');
        setUploadProgress(0);
    };

    const handleDrop = (e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setDragOver(false);
        const f = e.dataTransfer.files[0];
        handleFileSelect(f ?? null);
    };

    const canSubmit = () => {
        if (!name.trim() || !projectId) return false;
        if (tab === 'minio') {
            if (mode === 'create') return !!file || !!uploadedUrl;
            return true; // edit 模式保留原 url
        }
        return !!gitUrl.trim();
    };

    const handleSubmit = async () => {
        if (!canSubmit()) return;
        setSubmitting(true);
        try {
            if (mode === 'create') {
                const sourceType: SourceType = tab === 'minio' ? 'MINIO' : 'GIT';

                let sourceUrl = '';

                // 处理 MinIO 模式的自动上传
                if (tab === 'minio') {
                    if (uploadedUrl) {
                        sourceUrl = uploadedUrl;
                    } else if (file) {
                        setUploading(true);
                        setUploadProgress(0);
                        const fd = new FormData();
                        fd.append('file', file);

                        const uploadRes = await uploadSpiderZip(fd, (evt: any) => {
                            if (evt.total) {
                                setUploadProgress(Math.round((evt.loaded / evt.total) * 100));
                            }
                        });

                        setUploading(false);

                        if (uploadRes.code === 200 && uploadRes.data?.source_url) {
                            sourceUrl = uploadRes.data.source_url;
                            setUploadedUrl(sourceUrl);
                            setUploadProgress(100);
                        } else {
                            showToast(uploadRes.message || 'ZIP 上传失败', 'error');
                            setSubmitting(false);
                            return; // 上传失败则中断创建
                        }
                    }
                } else {
                    sourceUrl = `${gitUrl.trim()}@${gitBranch.trim() || 'main'}`;
                }

                const payload: SpiderCreatePayload = {
                    name: name.trim(),
                    language: language,
                    command: command.trim() || undefined,
                    project_id: projectId,
                    source_type: sourceType,
                    source_url: sourceUrl,
                };
                const res = await createSpider(payload);
                if (res.code === 200) {
                    showToast('爬虫创建成功', 'success');
                    onSaved();
                } else {
                    showToast(res.message || '创建失败', 'error');
                }
            } else if (initial) {
                const res = await updateSpider(initial.id, {
                    name: name.trim(),
                    language: language,
                    command: command.trim() || undefined,
                    project_id: projectId,
                });
                if (res.code === 200) {
                    showToast('爬虫信息已更新', 'success');
                    onSaved();
                } else {
                    showToast(res.message || '更新失败', 'error');
                }
            }
        } catch {
            showToast('操作出现异常', 'error');
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <>
            <div className="sv-drawer-overlay" onClick={onClose} />
            <div className="sv-drawer glass-panel" onClick={e => e.stopPropagation()}>
                {/* 头部 */}
                <div className="sv-drawer-header">
                    <h3>{mode === 'create' ? '新建爬虫' : '编辑爬虫'}</h3>
                    <button className="sv-drawer-close" onClick={onClose}><X size={18} /></button>
                </div>

                {/* Body */}
                <div className="sv-drawer-body">
                    {/* Tabs（编辑模式不允许切换来源类型） */}
                    {mode === 'create' && (
                        <div className="sv-tabs">
                            <button
                                className={`sv-tab ${tab === 'minio' ? 'active' : ''}`}
                                onClick={() => setTab('minio')}
                            >
                                <UploadCloud size={14} /> 本地上传 (MinIO)
                            </button>
                            <button
                                className={`sv-tab ${tab === 'git' ? 'active' : ''}`}
                                onClick={() => setTab('git')}
                            >
                                <GitBranch size={14} /> Git 仓库
                            </button>
                        </div>
                    )}

                    {/* 来源配置 */}
                    {tab === 'minio' ? (
                        <>
                            {/* 拖拽上传区 */}
                            {!uploadedUrl && (
                                <div
                                    className={`sv-dragger ${dragOver ? 'drag-over' : ''}`}
                                    onClick={() => fileInputRef.current?.click()}
                                    onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                                    onDragLeave={() => setDragOver(false)}
                                    onDrop={handleDrop}
                                >
                                    <UploadCloud size={36} className="sv-dragger-icon" />
                                    <div className="sv-dragger-title">点击或拖拽上传 ZIP 包</div>
                                    <div className="sv-dragger-hint">仅支持 .zip 格式</div>
                                    <input
                                        ref={fileInputRef}
                                        type="file"
                                        accept=".zip"
                                        onChange={e => handleFileSelect(e.target.files?.[0] ?? null)}
                                    />
                                </div>
                            )}

                            {/* 已选文件 */}
                            {file && !uploadedUrl && (
                                <div className="sv-file-selected">
                                    <UploadCloud size={15} />
                                    <span className="sv-file-name">{file.name}</span>
                                    <button
                                        className="sv-file-clear"
                                        onClick={() => { setFile(null); setUploadProgress(0); }}
                                    >
                                        <X size={14} />
                                    </button>
                                </div>
                            )}

                            {/* 进度条 */}
                            {uploading || (uploadProgress > 0 && uploadProgress < 100) ? (
                                <div className="sv-progress-wrap">
                                    <div className="sv-progress-label">
                                        <span>上传中...</span>
                                        <span>{uploadProgress}%</span>
                                    </div>
                                    <div className="sv-progress-bar-bg">
                                        <div
                                            className="sv-progress-bar-fill"
                                            style={{ width: `${uploadProgress}%` }}
                                        />
                                    </div>
                                </div>
                            ) : null}

                            {/* 上传成功 */}
                            {uploadedUrl && (
                                <div className="sv-file-selected" style={{ marginBottom: 16 }}>
                                    <Check size={15} />
                                    <span className="sv-file-name">{uploadedUrl}</span>
                                    <button
                                        className="sv-file-clear"
                                        onClick={() => { setFile(null); setUploadedUrl(''); setUploadProgress(0); }}
                                    >
                                        <X size={14} />
                                    </button>
                                </div>
                            )}
                        </>
                    ) : (
                        <>
                            <div className="sv-field">
                                <label>仓库地址 *</label>
                                <input
                                    type="url"
                                    value={gitUrl}
                                    onChange={e => setGitUrl(e.target.value)}
                                    placeholder="https://github.com/your/repo.git"
                                />
                            </div>
                            <div className="sv-field">
                                <label>分支名称</label>
                                <input
                                    type="text"
                                    value={gitBranch}
                                    onChange={e => setGitBranch(e.target.value)}
                                    placeholder="main"
                                />
                            </div>
                        </>
                    )}

                    {/* 公共字段 */}
                    <div className="sv-field">
                        <label>归属项目 *</label>
                        <CustomSelect
                            value={projectId}
                            onChange={setProjectId}
                            options={projects.length > 0
                                ? projects.map(p => ({ label: p.name, value: p.project_id }))
                                : [{ label: '默认项目 (请先去创建正式项目)', value: 'default' }]}
                        />
                    </div>
                    <div className="sv-field">
                        <label>爬虫名称 *</label>
                        <input
                            type="text"
                            value={name}
                            onChange={e => setName(e.target.value)}
                            placeholder="例如：电商价格监控"
                            autoFocus={mode === 'edit'}
                        />
                    </div>
                    <div className="sv-field">
                        <label>运行环境 (Language) *</label>
                        <CustomSelect
                            value={language}
                            onChange={handleLanguageChange}
                            options={languageOptions}
                        />
                    </div>
                    <div className="sv-field">
                        <label>启动命令（可选，入口文件即可）</label>
                        <input
                            type="text"
                            value={command}
                            onChange={e => setCommand(e.target.value)}
                            placeholder="例如：main.py"
                        />
                    </div>
                </div>

                {/* Footer */}
                <div className="sv-drawer-footer">
                    <button className="sv-btn sv-btn-ghost" onClick={onClose}>取消</button>
                    <button
                        className="sv-btn sv-btn-primary"
                        disabled={!canSubmit() || submitting}
                        onClick={handleSubmit}
                    >
                        {submitting
                            ? <><Loader2 size={14} className="spin" /> 保存中...</>
                            : (mode === 'create' ? '创建爬虫' : '保存修改')
                        }
                    </button>
                </div>
            </div>
        </>
    );
}

// ─────────────────────────────────────────────────
// 运行弹窗
// ─────────────────────────────────────────────────
interface RunModalProps {
    spider: SpiderItem;
    nodes: SpiderNode[];
    onClose: () => void;
    showToast: (msg: string, type?: ToastType) => void;
}

function RunSpiderModal({ spider, nodes, onClose, showToast }: RunModalProps) {
    const [selectedNodes, setSelectedNodes] = useState<string[]>([]);
    const [submitting, setSubmitting] = useState(false);

    const toggleNode = (nodeId: string) => {
        setSelectedNodes(prev =>
            prev.includes(nodeId) ? prev.filter(id => id !== nodeId) : [...prev, nodeId]
        );
    };

    const handleRun = async () => {
        setSubmitting(true);
        try {
            const res = await runSpider(spider.id, { target_nodes: selectedNodes });
            if (res.code === 200) {
                showToast(`任务已入队 (${res.data?.task_id ?? '...'})`, 'success');
                onClose();
            } else {
                showToast(res.message || '触发失败', 'error');
            }
        } catch {
            showToast('请求异常', 'error');
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="sv-overlay" onClick={onClose}>
            <div className="sv-modal glass-panel" onClick={e => e.stopPropagation()}>
                <div className="sv-modal-header">
                    <h3><Play size={16} /> 触发运行</h3>
                    <button className="sv-drawer-close" onClick={onClose}><X size={17} /></button>
                </div>

                <div className="sv-modal-body">
                    <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
                        爬虫：<strong style={{ color: '#fff' }}>{spider.name}</strong>
                    </p>

                    <div className="sv-field">
                        <label>指定运行节点（不选则随机调度）</label>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {nodes.length === 0 ? (
                                <div style={{ color: 'var(--text-muted)', fontSize: '0.82rem', padding: '8px 0' }}>
                                    暂无在线节点，将走公共队列调度
                                </div>
                            ) : (
                                nodes.map(node => (
                                    <label
                                        key={node.node_id}
                                        style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: 10,
                                            padding: '10px 14px',
                                            border: `1px solid ${selectedNodes.includes(node.node_id) ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                                            borderRadius: 8,
                                            background: selectedNodes.includes(node.node_id) ? 'rgba(59,130,246,0.08)' : 'rgba(255,255,255,0.02)',
                                            cursor: 'pointer',
                                            transition: 'all 0.15s',
                                            fontSize: '0.85rem',
                                        }}
                                    >
                                        <input
                                            type="checkbox"
                                            checked={selectedNodes.includes(node.node_id)}
                                            onChange={() => toggleNode(node.node_id)}
                                            style={{ accentColor: 'var(--accent-primary)' }}
                                        />
                                        <span className="sv-node-dot" style={{ background: node.status === 'online' ? '#4ade80' : '#f87171' }} />
                                        <span style={{ fontWeight: 500, color: '#fff' }}>
                                            {node.name || node.node_id}
                                        </span>
                                        <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                                            {node.ip} · CPU {node.cpu_usage.toFixed(0)}%
                                        </span>
                                    </label>
                                ))
                            )}
                        </div>
                        <div className="sv-select-hint">
                            {selectedNodes.length === 0
                                ? '未选节点，任务将进入公共队列由任意 Worker 竞争执行'
                                : `已选节点：${selectedNodes.map(id => nodes.find(n => n.node_id === id)?.name || id).join('、')}`}
                        </div>
                    </div>
                </div>

                <div className="sv-modal-footer">
                    <button className="sv-btn sv-btn-ghost" onClick={onClose}>取消</button>
                    <button
                        className="sv-btn sv-btn-primary"
                        onClick={handleRun}
                        disabled={submitting}
                    >
                        {submitting
                            ? <><Loader2 size={14} className="spin" /> 提交中...</>
                            : <><Play size={14} /> 确认运行</>
                        }
                    </button>
                </div>
            </div>
        </div>
    );
}



// ─────────────────────────────────────────────────
// 主视图
// ─────────────────────────────────────────────────
export default function SpidersView() {
    const location = useLocation();
    const [spiders, setSpiders] = useState<SpiderItem[]>([]);
    const [nodes, setNodes] = useState<SpiderNode[]>([]);
    const [projects, setProjects] = useState<ProjectItem[]>([]);
    const [loading, setLoading] = useState(false);

    // 选中的蜘蛛的Id（从 URL 参数中获取）
    const [highlightId, setHighlightId] = useState<number | null>(null);

    // 各弹层状态
    const [drawerMode, setDrawerMode] = useState<{ visible: boolean; mode: DrawerMode; target: SpiderItem | null }>({
        visible: false, mode: 'create', target: null,
    });
    const [runTarget, setRunTarget] = useState<SpiderItem | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<SpiderItem | null>(null);
    const [codeTarget, setCodeTarget] = useState<SpiderItem | null>(null);
    const [historyTarget, setHistoryTarget] = useState<SpiderItem | null>(null);

    const { toasts, show: showToast } = useToast();

    const loadSpiders = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetchSpiderList();
            if (res.code === 200 && res.data) setSpiders(res.data);
        } catch { /* silent */ }
        setLoading(false);
    }, []);

    const loadNodes = useCallback(async () => {
        try {
            const res = await fetchNodeList();
            if (res.code === 200 && res.data) setNodes(res.data);
        } catch { /* silent */ }
    }, []);

    const loadProjects = useCallback(async () => {
        try {
            const res = await fetchProjectList();
            if (res.code === 200 && res.data) setProjects(res.data);
        } catch { /* silent */ }
    }, []);

    useEffect(() => {
        loadSpiders();
        loadNodes();
        loadProjects();
    }, [loadSpiders, loadNodes, loadProjects]);

    // 处理从外部跳转过来的情况 (例如 ?id=123)
    useEffect(() => {
        const query = new URLSearchParams(location.search);
        const idParam = query.get('id');
        if (idParam && !isNaN(Number(idParam))) {
            const id = Number(idParam);
            setHighlightId(id);
            // 稍等一会让表格渲染完成
            setTimeout(() => {
                const el = document.getElementById(`spider-row-${id}`);
                if (el) {
                    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }, 300);

            // 3秒后取消高亮
            setTimeout(() => {
                setHighlightId(null);
            }, 3000);
        }
    }, [location.search, spiders.length]);

    const openCreate = () => setDrawerMode({ visible: true, mode: 'create', target: null });
    const openEdit = (s: SpiderItem, e: React.MouseEvent) => {
        e.stopPropagation();
        setDrawerMode({ visible: true, mode: 'edit', target: s });
    };
    const openRun = (s: SpiderItem, e: React.MouseEvent) => {
        e.stopPropagation();
        loadNodes(); // 刷新节点
        setRunTarget(s);
    };
    const openDelete = (s: SpiderItem, e: React.MouseEvent) => {
        e.stopPropagation();
        setDeleteTarget(s);
    };
    const openCode = (s: SpiderItem, e: React.MouseEvent) => {
        e.stopPropagation();
        setCodeTarget(s);
    };
    const openHistory = (s: SpiderItem, e: React.MouseEvent) => {
        e.stopPropagation();
        setHistoryTarget(s);
    };

    const handleSaved = () => {
        setDrawerMode({ visible: false, mode: 'create', target: null });
        loadSpiders();
    };

    return (
        <div className="sv-container">
            {/* 工具栏 */}
            <div className="sv-toolbar glass-panel">
                <div className="sv-toolbar-left">
                    <h2><Bug size={20} /> 爬虫管理</h2>
                    <span className="sv-count">{spiders.length} 个爬虫</span>
                </div>
                <div className="sv-toolbar-right">
                    <PermissionGuard roles={['admin', 'developer']}>
                        <button className="sv-btn sv-btn-primary" onClick={openCreate}>
                            <Plus size={15} /> 新建爬虫
                        </button>
                    </PermissionGuard>
                </div>
            </div>

            {/* 列表 */}
            <div className="sv-table-wrap glass-panel">
                {loading && spiders.length === 0 ? (
                    <div className="sv-empty">
                        <Loader2 size={32} style={{ animation: 'spin 1s linear infinite' }} />
                        <p>加载中...</p>
                    </div>
                ) : spiders.length === 0 ? (
                    <div className="sv-empty">
                        <Bug size={48} strokeWidth={1} />
                        <p>暂无爬虫，点击右上角「新建爬虫」添加</p>
                    </div>
                ) : (
                    <table className="sv-table">
                        <thead>
                            <tr>
                                <th>爬虫名称</th>
                                <th>所属项目</th>
                                <th>来源类型</th>
                                <th>源地址</th>
                                <th>创建时间</th>
                                <th style={{ width: 165 }}>操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {spiders.map(s => (
                                <tr
                                    key={s.id}
                                    id={`spider-row-${s.id}`}
                                    className={highlightId === s.id ? 'highlight-row' : ''}
                                    style={{
                                        transition: 'background-color 0.5s',
                                        backgroundColor: highlightId === s.id
                                            ? 'rgba(56, 189, 248, 0.15)'
                                            : 'transparent'
                                    }}
                                >
                                    <td>
                                        <span className="sv-spider-name">
                                            {s.name}
                                        </span>
                                        {s.command && (
                                            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 2, fontFamily: 'monospace' }}>
                                                $ {s.command}
                                            </div>
                                        )}
                                    </td>
                                    <td>
                                        <span className="mono-sm" title={s.project_id}>
                                            {projects.find(p => p.project_id === s.project_id)?.name || s.project_id}
                                        </span>
                                    </td>
                                    <td><SourceTag type={s.source_type} /></td>
                                    <td><CopyableUrl url={s.source_url} /></td>
                                    <td><span className="mono-sm">{formatDateTime(s.created_at)}</span></td>
                                    <td>
                                        <div className="sv-ops">
                                            {s.source_type === 'MINIO' && (
                                                <button
                                                    className="sv-btn-icon code"
                                                    title="查看代码"
                                                    onClick={e => openCode(s, e)}
                                                >
                                                    <Code size={14} />
                                                </button>
                                            )}
                                            <button
                                                className="sv-btn-icon history"
                                                title="任务历史"
                                                onClick={e => openHistory(s, e)}
                                            >
                                                <History size={14} />
                                            </button>
                                            <button
                                                className="sv-btn-icon run"
                                                title="运行"
                                                onClick={e => openRun(s, e)}
                                            >
                                                <Play size={15} />
                                            </button>
                                            <PermissionGuard roles={['admin', 'developer']}>
                                                <button
                                                    className="sv-btn-icon edit"
                                                    title="编辑"
                                                    onClick={e => openEdit(s, e)}
                                                >
                                                    <Pencil size={14} />
                                                </button>
                                                <button
                                                    className="sv-btn-icon del"
                                                    title="删除"
                                                    onClick={e => openDelete(s, e)}
                                                >
                                                    <Trash2 size={14} />
                                                </button>
                                            </PermissionGuard>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* 新建/编辑 抽屉 */}
            {drawerMode.visible && (
                <SpiderDrawer
                    mode={drawerMode.mode}
                    initial={drawerMode.target}
                    onClose={() => setDrawerMode({ ...drawerMode, visible: false })}
                    onSaved={handleSaved}
                    showToast={showToast}
                    projects={projects}
                />
            )}

            {/* 运行弹窗 */}
            {runTarget && (
                <RunSpiderModal
                    spider={runTarget}
                    nodes={nodes}
                    onClose={() => setRunTarget(null)}
                    showToast={showToast}
                />
            )}

            {/* 删除确认 */}
            <DangerConfirmModal
                open={!!deleteTarget}
                title="删除爬虫"
                description={<>确定删除爬虫 <strong>{deleteTarget?.name}</strong> 吗？</>}
                onCancel={() => setDeleteTarget(null)}
                onConfirm={async () => {
                    if (!deleteTarget) return;
                    try {
                        const res = await deleteSpider(deleteTarget.id);
                        if (res.code === 200) {
                            showToast('爬虫已删除', 'success');
                            setDeleteTarget(null);
                            loadSpiders();
                        } else {
                            showToast(res.message || '删除失败', 'error');
                        }
                    } catch {
                        showToast('请求异常', 'error');
                    }
                }}
            />

            {/* 代码编辑器 */}
            {codeTarget && (
                <CodeEditorModal
                    spider={codeTarget}
                    onClose={() => setCodeTarget(null)}
                    showToast={showToast}
                />
            )}

            {/* 任务历史 */}
            {historyTarget && (
                <TaskHistoryModal
                    spider={historyTarget}
                    onClose={() => setHistoryTarget(null)}
                    showToast={showToast}
                />
            )}

            {/* Toast 提示 */}
            {toasts.map(t => (
                <div key={t.id} className={`sv-toast ${t.type}`}>
                    {t.type === 'success' && <Check size={14} />}
                    {t.type === 'error' && <X size={14} />}
                    {t.msg}
                </div>
            ))}
        </div>
    );
}
