import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Pencil, Trash2, FolderOpen, Bug, X } from 'lucide-react';
import { fetchProjectList, createProject, updateProject, deleteProject, type ProjectItem } from '@/api/project';
import classNames from 'classnames';
import './ProjectManagement.css';

export default function ProjectManagement() {
    const [projects, setProjects] = useState<ProjectItem[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    // 弹窗状态
    const [modal, setModal] = useState<{ type: 'create' | 'edit'; visible: boolean }>({ type: 'create', visible: false });
    const [formData, setFormData] = useState({ name: '', description: '' });
    const [confirmDelete, setConfirmDelete] = useState(false);

    const loadProjects = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetchProjectList();
            if (res.code === 200 && res.data) {
                setProjects(res.data);
            }
        } catch { /* silent */ }
        setLoading(false);
    }, []);

    useEffect(() => { loadProjects(); }, [loadProjects]);

    const selectedProject = projects.find(p => p.project_id === selectedId) || null;

    const openCreate = () => {
        setFormData({ name: '', description: '' });
        setModal({ type: 'create', visible: true });
    };

    const openEdit = () => {
        if (!selectedProject) return;
        setFormData({ name: selectedProject.name, description: selectedProject.description || '' });
        setModal({ type: 'edit', visible: true });
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!formData.name.trim()) return;

        try {
            if (modal.type === 'create') {
                await createProject({ name: formData.name.trim(), description: formData.description.trim() || undefined });
            } else if (selectedId) {
                await updateProject(selectedId, { name: formData.name.trim(), description: formData.description.trim() || undefined });
            }
            setModal({ ...modal, visible: false });
            await loadProjects();
        } catch { /* silent */ }
    };

    const handleDelete = async () => {
        if (!selectedId) return;
        try {
            await deleteProject(selectedId);
            setSelectedId(null);
            setConfirmDelete(false);
            await loadProjects();
        } catch { /* silent */ }
    };

    return (
        <div className="pm-container">
            {/* 顶部工具栏 */}
            <div className="pm-toolbar glass-panel">
                <div className="pm-toolbar-left">
                    <h2><FolderOpen size={22} /> 项目管理</h2>
                    <span className="pm-count">{projects.length} 个项目</span>
                </div>
                <div className="pm-toolbar-right">
                    {selectedProject && (
                        <>
                            <button className="pm-btn pm-btn-edit" onClick={openEdit}>
                                <Pencil size={14} /> 修改项目
                            </button>
                            <button className="pm-btn pm-btn-danger" onClick={() => setConfirmDelete(true)}>
                                <Trash2 size={14} /> 删除项目
                            </button>
                        </>
                    )}
                    <button className="pm-btn pm-btn-primary" onClick={openCreate}>
                        <Plus size={16} /> 新增项目
                    </button>
                </div>
            </div>

            {/* 项目列表 */}
            <div className="pm-list glass-panel">
                {loading && projects.length === 0 ? (
                    <div className="pm-empty">加载中...</div>
                ) : projects.length === 0 ? (
                    <div className="pm-empty">
                        <FolderOpen size={48} strokeWidth={1} />
                        <p>暂无项目，点击右上角「新增项目」创建</p>
                    </div>
                ) : (
                    <table className="pm-table">
                        <thead>
                            <tr>
                                <th style={{ width: 40 }}></th>
                                <th>项目 ID</th>
                                <th>名称</th>
                                <th>描述</th>
                                <th>爬虫数</th>
                                <th>创建时间</th>
                            </tr>
                        </thead>
                        <tbody>
                            {projects.map(p => (
                                <tr
                                    key={p.project_id}
                                    className={classNames({ selected: selectedId === p.project_id })}
                                    onClick={() => setSelectedId(selectedId === p.project_id ? null : p.project_id)}
                                >
                                    <td>
                                        <div className={classNames('pm-radio', { active: selectedId === p.project_id })} />
                                    </td>
                                    <td className="mono">{p.project_id}</td>
                                    <td className="pm-name">{p.name}</td>
                                    <td className="pm-desc">{p.description || '—'}</td>
                                    <td>
                                        <span className="pm-spider-count">
                                            <Bug size={12} /> {p.spider_count}
                                        </span>
                                    </td>
                                    <td className="mono">{new Date(p.created_at).toLocaleDateString()}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* 创建/编辑弹窗 */}
            {modal.visible && (
                <div className="pm-overlay" onClick={() => setModal({ ...modal, visible: false })}>
                    <div className="pm-modal glass-panel" onClick={e => e.stopPropagation()}>
                        <div className="pm-modal-header">
                            <h3>{modal.type === 'create' ? '新增项目' : '修改项目'}</h3>
                            <button className="pm-close" onClick={() => setModal({ ...modal, visible: false })}>
                                <X size={18} />
                            </button>
                        </div>
                        <form onSubmit={handleSubmit}>
                            <div className="pm-field">
                                <label>项目名称 *</label>
                                <input
                                    type="text"
                                    value={formData.name}
                                    onChange={e => setFormData({ ...formData, name: e.target.value })}
                                    placeholder="例如: 电商爬虫集合"
                                    required
                                    autoFocus
                                />
                            </div>
                            <div className="pm-field">
                                <label>项目描述</label>
                                <textarea
                                    value={formData.description}
                                    onChange={e => setFormData({ ...formData, description: e.target.value })}
                                    placeholder="可选的简要描述..."
                                    rows={3}
                                />
                            </div>
                            <div className="pm-modal-actions">
                                <button type="button" className="pm-btn pm-btn-ghost" onClick={() => setModal({ ...modal, visible: false })}>
                                    取消
                                </button>
                                <button type="submit" className="pm-btn pm-btn-primary" disabled={!formData.name.trim()}>
                                    {modal.type === 'create' ? '创建' : '保存'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* 删除确认弹窗 */}
            {confirmDelete && selectedProject && (
                <div className="pm-overlay" onClick={() => setConfirmDelete(false)}>
                    <div className="pm-modal glass-panel pm-modal-sm" onClick={e => e.stopPropagation()}>
                        <div className="pm-modal-header">
                            <h3>确认删除</h3>
                        </div>
                        <p className="pm-confirm-text">
                            确定删除项目 <strong>{selectedProject.name}</strong> 吗？
                            {selectedProject.spider_count > 0 && (
                                <span className="pm-warn">该项目下有 {selectedProject.spider_count} 个爬虫，将一并删除。</span>
                            )}
                        </p>
                        <div className="pm-modal-actions">
                            <button className="pm-btn pm-btn-ghost" onClick={() => setConfirmDelete(false)}>取消</button>
                            <button className="pm-btn pm-btn-danger" onClick={handleDelete}>确认删除</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
