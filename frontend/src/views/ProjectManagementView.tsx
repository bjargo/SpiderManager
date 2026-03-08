import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { PackageOpen, Plus, Pencil, Trash2, X, Check, Loader2, FolderOpen } from 'lucide-react';
import { fetchProjectList, createProject, updateProject, deleteProject, type ProjectItem } from '@/api/project';
import { fetchSpiderList } from '@/api/spider';
import type { SpiderItem } from '@/types/spider';
import classNames from 'classnames';
import './ProjectManagement.css';

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

interface DrawerProps {
    mode: DrawerMode;
    initial?: ProjectItem | null;
    onClose: () => void;
    onSaved: () => void;
    showToast: (msg: string, type?: ToastType) => void;
}

function ProjectDrawer({ mode, initial, onClose, onSaved, showToast }: DrawerProps) {
    const [name, setName] = useState(initial?.name ?? '');
    const [description, setDescription] = useState(initial?.description ?? '');
    const [submitting, setSubmitting] = useState(false);

    const canSubmit = () => !!name.trim();

    const handleSubmit = async () => {
        if (!canSubmit()) return;
        setSubmitting(true);
        try {
            if (mode === 'create') {
                const res = await createProject({ name: name.trim(), description: description.trim() || undefined });
                if (res.code === 200) {
                    showToast('项目创建成功', 'success');
                    onSaved();
                } else {
                    showToast(res.message || '创建失败', 'error');
                }
            } else if (initial) {
                const res = await updateProject(initial.project_id, {
                    name: name.trim(),
                    description: description.trim() || undefined,
                });
                if (res.code === 200) {
                    showToast('项目信息已更新', 'success');
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
            <div className="pm-drawer-overlay" onClick={onClose} />
            <div className="pm-drawer glass-panel" onClick={e => e.stopPropagation()}>
                <div className="pm-drawer-header">
                    <h3>{mode === 'create' ? '新建项目' : '编辑项目'}</h3>
                    <button className="pm-drawer-close" onClick={onClose}><X size={18} /></button>
                </div>
                <div className="pm-drawer-body">
                    <div className="pm-field">
                        <label>项目名称 *</label>
                        <input
                            type="text"
                            value={name}
                            onChange={e => setName(e.target.value)}
                            placeholder="例如：电商数据抓取项目"
                            autoFocus={mode === 'create'}
                        />
                    </div>
                    <div className="pm-field">
                        <label>描述</label>
                        <textarea
                            value={description}
                            onChange={e => setDescription(e.target.value)}
                            placeholder="项目描述说明..."
                            rows={4}
                        />
                    </div>
                </div>
                <div className="pm-drawer-footer">
                    <button className="pm-btn pm-btn-ghost" onClick={onClose}>取消</button>
                    <button
                        className="pm-btn pm-btn-primary"
                        disabled={!canSubmit() || submitting}
                        onClick={handleSubmit}
                    >
                        {submitting
                            ? <><Loader2 size={14} className="spin" /> 保存中...</>
                            : (mode === 'create' ? '创建项目' : '保存修改')
                        }
                    </button>
                </div>
            </div>
        </>
    );
}

// ─────────────────────────────────────────────────
// 删除确认弹窗
// ─────────────────────────────────────────────────
interface DeleteModalProps {
    project: ProjectItem;
    onClose: () => void;
    onDeleted: () => void;
    showToast: (msg: string, type?: ToastType) => void;
}

function DeleteModal({ project, onClose, onDeleted, showToast }: DeleteModalProps) {
    const [submitting, setSubmitting] = useState(false);

    const handleDelete = async () => {
        setSubmitting(true);
        try {
            const res = await deleteProject(project.project_id);
            if (res.code === 200) {
                showToast('项目已删除', 'success');
                onDeleted();
            } else {
                showToast(res.message || '删除失败', 'error');
            }
        } catch {
            showToast('请求异常', 'error');
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="pm-overlay" onClick={onClose}>
            <div className="pm-modal glass-panel" onClick={e => e.stopPropagation()}>
                <div className="pm-modal-header">
                    <h3><Trash2 size={16} /> 确认删除</h3>
                    <button className="pm-drawer-close" onClick={onClose}><X size={17} /></button>
                </div>
                <div className="pm-modal-body">
                    <p className="pm-confirm-text">
                        确定删除项目 <strong style={{ color: '#fff' }}>{project.name}</strong> 吗？
                        这将同时删除该项目下关联的 <strong style={{ color: '#f87171' }}>{project.spider_count}</strong> 个爬虫，此操作不可撤销！
                    </p>
                </div>
                <div className="pm-modal-footer">
                    <button className="pm-btn pm-btn-ghost" onClick={onClose}>取消</button>
                    <button className="pm-btn pm-btn-danger" onClick={handleDelete} disabled={submitting}>
                        {submitting ? '删除中...' : '确认删除'}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─────────────────────────────────────────────────
// 关联爬虫弹窗
// ─────────────────────────────────────────────────
interface SpiderListModalProps {
    project: ProjectItem;
    spiders: SpiderItem[];
    onClose: () => void;
}

function SpiderListModal({ project, spiders, onClose }: SpiderListModalProps) {
    const navigate = useNavigate();
    // 过滤出该项目下的爬虫
    // 旧接口设计中可能都叫 default，这里我们尝试过滤
    // 但是现在的实现是 spider.project_id
    const projectSpiders = spiders.filter(s => s.project_id === project.project_id || (project.project_id === 'default' && s.project_id === 'default'));

    const handleSpiderClick = (spiderId: number) => {
        onClose();
        navigate(`/spiders?id=${spiderId}`);
    };

    return (
        <div className="pm-overlay" onClick={onClose}>
            <div className="pm-modal glass-panel pm-modal-large" onClick={e => e.stopPropagation()}>
                <div className="pm-modal-header">
                    <h3><FolderOpen size={16} /> 项目【{project.name}】下的爬虫</h3>
                    <button className="pm-drawer-close" onClick={onClose}><X size={17} /></button>
                </div>
                <div className="pm-modal-body" style={{ maxHeight: '60vh', overflowY: 'auto' }}>
                    {projectSpiders.length === 0 ? (
                        <div className="pm-empty-sm">暂无关联的爬虫</div>
                    ) : (
                        <table className="pm-table">
                            <thead>
                                <tr>
                                    <th>爬虫ID</th>
                                    <th>名称</th>
                                    <th>创建时间</th>
                                </tr>
                            </thead>
                            <tbody>
                                {projectSpiders.map(s => (
                                    <tr key={s.id}>
                                        <td className="mono-sm">{s.id}</td>
                                        <td>
                                            <a
                                                href="#!"
                                                className="pm-spider-link"
                                                onClick={(e) => { e.preventDefault(); handleSpiderClick(s.id); }}
                                            >
                                                {s.name}
                                            </a>
                                        </td>
                                        <td className="mono-sm">{formatDateTime(s.created_at)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            </div>
        </div>
    );
}

// ─────────────────────────────────────────────────
// 主视图
// ─────────────────────────────────────────────────
export default function ProjectManagementView() {
    const [projects, setProjects] = useState<ProjectItem[]>([]);
    const [allSpiders, setAllSpiders] = useState<SpiderItem[]>([]);
    const [loading, setLoading] = useState(false);

    const [drawerMode, setDrawerMode] = useState<{ visible: boolean; mode: DrawerMode; target: ProjectItem | null }>({
        visible: false, mode: 'create', target: null,
    });
    const [deleteTarget, setDeleteTarget] = useState<ProjectItem | null>(null);
    const [viewSpidersTarget, setViewSpidersTarget] = useState<ProjectItem | null>(null);

    const { toasts, show: showToast } = useToast();

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [projRes, spiderRes] = await Promise.all([
                fetchProjectList(),
                fetchSpiderList()
            ]);
            if (projRes.code === 200 && projRes.data) setProjects(projRes.data);
            if (spiderRes.code === 200 && spiderRes.data) setAllSpiders(spiderRes.data);
        } catch { /* silent */ }
        setLoading(false);
    }, []);

    useEffect(() => {
        loadData();
    }, [loadData]);

    const openCreate = () => setDrawerMode({ visible: true, mode: 'create', target: null });
    const openEdit = (p: ProjectItem) => setDrawerMode({ visible: true, mode: 'edit', target: p });
    const openDelete = (p: ProjectItem) => setDeleteTarget(p);
    const openSpiders = (p: ProjectItem) => setViewSpidersTarget(p);

    const handleSaved = () => {
        setDrawerMode({ visible: false, mode: 'create', target: null });
        loadData();
    };

    return (
        <div className="pm-container">
            {/* 工具栏 */}
            <div className="pm-toolbar glass-panel">
                <div className="pm-toolbar-left">
                    <h2><PackageOpen size={20} /> 项目管理</h2>
                    <span className="pm-count">{projects.length} 个项目</span>
                </div>
                <div className="pm-toolbar-right">
                    <button className="pm-btn pm-btn-primary" onClick={openCreate}>
                        <Plus size={15} /> 新建项目
                    </button>
                </div>
            </div>

            {/* 列表 */}
            <div className="pm-table-wrap glass-panel">
                {loading && projects.length === 0 ? (
                    <div className="pm-empty">
                        <Loader2 size={32} style={{ animation: 'spin 1s linear infinite' }} />
                        <p>加载中...</p>
                    </div>
                ) : projects.length === 0 ? (
                    <div className="pm-empty">
                        <PackageOpen size={48} strokeWidth={1} />
                        <p>暂无项目，点击右上角「新建项目」添加</p>
                    </div>
                ) : (
                    <table className="pm-table">
                        <thead>
                            <tr>
                                <th>项目名称</th>
                                <th>描述</th>
                                <th>爬虫数</th>
                                <th>创建时间</th>
                                <th style={{ width: 120 }}>操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {projects.map(p => (
                                <tr key={p.project_id}>
                                    <td>
                                        <span className="pm-project-name">
                                            {p.name}
                                        </span>
                                        <div className="pm-project-id mono-sm">{p.project_id}</div>
                                    </td>
                                    <td><span className="pm-desc">{p.description || '-'}</span></td>
                                    <td>
                                        <span
                                            className={classNames("pm-tag", p.spider_count > 0 ? 'pm-tag-blue' : 'pm-tag-gray')}
                                            onClick={() => openSpiders(p)}
                                            style={{ cursor: 'pointer' }}
                                            title="点击查看爬虫"
                                        >
                                            {p.spider_count}
                                        </span>
                                    </td>
                                    <td><span className="mono-sm">{formatDateTime(p.created_at)}</span></td>
                                    <td>
                                        <div className="pm-ops">
                                            <button
                                                className="pm-btn-icon edit"
                                                title="编辑"
                                                onClick={() => openEdit(p)}
                                            >
                                                <Pencil size={14} />
                                            </button>
                                            <button
                                                className="pm-btn-icon del"
                                                title="删除"
                                                onClick={() => openDelete(p)}
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* 新建/编辑 项目抽屉 */}
            {drawerMode.visible && (
                <ProjectDrawer
                    mode={drawerMode.mode}
                    initial={drawerMode.target}
                    onClose={() => setDrawerMode({ ...drawerMode, visible: false })}
                    onSaved={handleSaved}
                    showToast={showToast}
                />
            )}

            {/* 删除确认 */}
            {deleteTarget && (
                <DeleteModal
                    project={deleteTarget}
                    onClose={() => setDeleteTarget(null)}
                    onDeleted={() => { setDeleteTarget(null); loadData(); }}
                    showToast={showToast}
                />
            )}

            {/* 爬虫列弹窗 */}
            {viewSpidersTarget && (
                <SpiderListModal
                    project={viewSpidersTarget}
                    spiders={allSpiders}
                    onClose={() => setViewSpidersTarget(null)}
                />
            )}

            {/* Toast 提示 */}
            {toasts.map(t => (
                <div key={t.id} className={`pm-toast ${t.type}`}>
                    {t.type === 'success' && <Check size={14} />}
                    {t.type === 'error' && <X size={14} />}
                    {t.msg}
                </div>
            ))}
        </div>
    );
}
