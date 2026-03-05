import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarClock, Plus, Trash2, Pencil, X, Loader2, Clock, Bug, Server } from 'lucide-react';
import { fetchCronTasks, addCronTask, deleteCronTask, updateCronTask, toggleCronTask } from '@/api/scheduler';
import { fetchSpiderList } from '@/api/spider';
import { fetchNodeList } from '@/api/node';
import { fetchProjectList, type ProjectItem } from '@/api/project';
import type { CronTaskResponse, CronTaskCreate, CronTaskUpdate } from '@/types/scheduler';
import type { SpiderItem } from '@/types/spider';
import type { SpiderNode } from '@/types/node';
import { CronEditor } from '@/components/CronEditor';
import cronstrue from 'cronstrue/i18n';
import './Scheduler.css';

// ─────────────────────────────────────────────────
// Helper: 获取人类可读的 Cron 描述
// ─────────────────────────────────────────────────
function getCronDescription(expr: string): string {
    if (!expr) return '';
    try {
        return cronstrue.toString(expr, { locale: 'zh_CN', use24HourTimeFormat: true });
    } catch {
        return '';
    }
}

// ─────────────────────────────────────────────────
// Toast 提示
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
// Switch 开关组件
// ─────────────────────────────────────────────────
interface SwitchProps {
    checked: boolean;
    loading?: boolean;
    onChange: (val: boolean) => void;
}

function Switch({ checked, loading, onChange }: SwitchProps) {
    return (
        <button
            type="button"
            className={`sch-switch ${checked ? 'on' : 'off'} ${loading ? 'loading' : ''}`}
            onClick={() => !loading && onChange(!checked)}
            disabled={loading}
        >
            <span className="sch-switch-thumb" />
        </button>
    );
}

// ─────────────────────────────────────────────────
// 格式化时间
// ─────────────────────────────────────────────────
function formatDateTime(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// ─────────────────────────────────────────────────
// 调度配置模态框
// ─────────────────────────────────────────────────
type ModalMode = 'create' | 'edit';

interface ScheduleModalProps {
    mode: ModalMode;
    initial?: CronTaskResponse | null;
    projects: ProjectItem[];
    spiders: SpiderItem[];
    nodes: SpiderNode[];
    tasks: CronTaskResponse[];
    onClose: () => void;
    onSaved: () => void;
    showToast: (msg: string, type?: ToastType) => void;
}

function ScheduleModal({ mode, initial, projects, spiders, nodes, tasks, onClose, onSaved, showToast }: ScheduleModalProps) {
    // 编辑模式：从 initial 的 spider_id 找到对应 project_id
    const initialProjectId = initial
        ? spiders.find(s => s.id === initial.spider_id)?.project_id ?? ''
        : '';

    const [projectId, setProjectId] = useState<string>(initialProjectId);
    const [spiderId, setSpiderId] = useState<number>(initial?.spider_id ?? 0);
    const [cronExpr, setCronExpr] = useState(initial?.cron_expr ?? '*/5 * * * *');
    const [description, setDescription] = useState(initial?.description ?? '');
    const [selectedNodes, setSelectedNodes] = useState<string[]>(initial?.target_node_ids ?? []);
    const [submitting, setSubmitting] = useState(false);

    // 当 cron 表达式改变时，自动更新说明描述
    useEffect(() => {
        const desc = getCronDescription(cronExpr);
        if (desc) setDescription(desc);
    }, [cronExpr]);

    // 按项目过滤爬虫
    const filteredSpiders = projectId
        ? spiders.filter(s => s.project_id === projectId)
        : spiders;

    // 当切换项目时，自动选中该项目下的第一个爬虫
    const handleProjectChange = (pid: string) => {
        setProjectId(pid);
        const projectSpiders = spiders.filter(s => s.project_id === pid);
        setSpiderId(projectSpiders.length > 0 ? projectSpiders[0].id : 0);
    };

    const toggleNode = (nodeId: string) => {
        setSelectedNodes(prev =>
            prev.includes(nodeId) ? prev.filter(id => id !== nodeId) : [...prev, nodeId]
        );
    };

    const handleSubmit = async () => {
        if (!spiderId) {
            showToast('请选择关联爬虫', 'error');
            return;
        }
        if (!cronExpr.trim()) {
            showToast('请填写 Cron 表达式', 'error');
            return;
        }

        // 简单的 Cron 格式校验：5 段空格分隔
        const cronParts = cronExpr.trim().split(/\s+/);
        if (cronParts.length !== 5) {
            showToast('Cron 表达式格式无效，需要 5 段（分 时 日 月 周）', 'error');
            return;
        }

        setSubmitting(true);
        try {
            if (mode === 'create') {
                const payload: CronTaskCreate = {
                    spider_id: spiderId,
                    cron_expr: cronExpr.trim(),
                    description: description.trim() || undefined,
                    enabled: true,
                    target_node_ids: selectedNodes.length > 0 ? selectedNodes : undefined,
                };
                await addCronTask(payload);
                showToast('调度任务创建成功');
            } else if (initial) {
                const payload: CronTaskUpdate = {
                    spider_id: spiderId,
                    cron_expr: cronExpr.trim(),
                    description: description.trim() || null,
                    target_node_ids: selectedNodes.length > 0 ? selectedNodes : null,
                };
                await updateCronTask(initial.job_id, payload);
                showToast('调度任务已更新');
            }
            onSaved();
        } catch {
            showToast(mode === 'create' ? '创建失败' : '更新失败', 'error');
        } finally {
            setSubmitting(false);
        }
    };

    // Calculate assigned tasks count for each node
    const getNodeAssignedCount = (nodeId: string) => {
        return tasks.filter(task => task.target_node_ids?.includes(nodeId)).length;
    };

    return (
        <div className="sch-overlay" onClick={onClose}>
            <div className="sch-modal glass-panel" onClick={e => e.stopPropagation()}>
                <div className="sch-modal-header">
                    <h3>
                        {mode === 'create'
                            ? <><Plus size={16} /> 新增调度</>
                            : <><Pencil size={16} /> 编辑调度</>
                        }
                    </h3>
                    <button className="sch-modal-close" onClick={onClose}><X size={17} /></button>
                </div>

                <div className="sch-modal-body">
                    {/* 所属项目选择器 */}
                    <div className="sch-field">
                        <label>📂 所属项目 *</label>
                        <select
                            className="sch-select"
                            value={projectId}
                            onChange={e => handleProjectChange(e.target.value)}
                        >
                            <option value="" disabled>请选择项目</option>
                            {projects.map(p => (
                                <option key={p.project_id} value={p.project_id}>{p.name}</option>
                            ))}
                        </select>
                    </div>

                    {/* 关联爬虫选择器 */}
                    <div className="sch-field">
                        <label><Bug size={14} /> 关联爬虫 *</label>
                        <select
                            className="sch-select"
                            value={spiderId}
                            onChange={e => setSpiderId(Number(e.target.value))}
                            disabled={!projectId}
                        >
                            {!projectId ? (
                                <option value={0} disabled>请先选择项目</option>
                            ) : filteredSpiders.length === 0 ? (
                                <option value={0} disabled>该项目下暂无爬虫</option>
                            ) : (
                                filteredSpiders.map(s => (
                                    <option key={s.id} value={s.id}>{s.name}</option>
                                ))
                            )}
                        </select>
                    </div>

                    {/* Cron 表达式 */}
                    <div className="sch-field">
                        <label><CalendarClock size={14} /> Cron 表达式 *</label>
                        <CronEditor value={cronExpr} onChange={setCronExpr} />
                    </div>

                    {/* 说明描述 */}
                    <div className="sch-field">
                        <label>说明描述 (根据 Cron 自动生成)</label>
                        <input
                            type="text"
                            className="sch-input"
                            value={description}
                            onChange={e => setDescription(e.target.value)}
                            placeholder="例如：每天凌晨抓取商品价格"
                        />
                    </div>

                    {/* 目标节点选择器 */}
                    <div className="sch-field">
                        <label><Server size={14} /> 目标节点（不选则随机调度）</label>
                        <div className="sch-node-list">
                            {nodes.length === 0 ? (
                                <div className="sch-node-empty">暂无在线节点，将走公共队列调度</div>
                            ) : (
                                nodes.map(node => {
                                    const assignedCount = getNodeAssignedCount(node.node_id);
                                    return (
                                        <label key={node.node_id} className={`sch-node-item ${selectedNodes.includes(node.node_id) ? 'selected' : ''}`}>
                                            <input
                                                type="checkbox"
                                                checked={selectedNodes.includes(node.node_id)}
                                                onChange={() => toggleNode(node.node_id)}
                                            />
                                            <span className="sch-node-dot" style={{ background: node.status === 'online' ? '#4ade80' : '#f87171' }} />
                                            <span className="sch-node-name">{node.name || node.node_id}</span>
                                            <span className="sch-node-info">{node.ip} · CPU {node.cpu_usage.toFixed(0)}% · 现已分配 {assignedCount} 个任务</span>
                                        </label>
                                    );
                                })
                            )}
                        </div>
                        <div className="sch-node-hint">
                            {selectedNodes.length === 0
                                ? '未选节点，任务将进入公共队列由任意 Worker 竞争执行'
                                : `已选 ${selectedNodes.length} 个节点`}
                        </div>
                    </div>
                </div>

                <div className="sch-modal-footer">
                    <button className="sch-btn sch-btn-ghost" onClick={onClose}>取消</button>
                    <button
                        className="sch-btn sch-btn-primary"
                        onClick={handleSubmit}
                        disabled={submitting}
                    >
                        {submitting
                            ? <><Loader2 size={14} className="spin" /> 提交中...</>
                            : (mode === 'create' ? '创建调度' : '保存修改')
                        }
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─────────────────────────────────────────────────
// 删除确认弹窗
// ─────────────────────────────────────────────────
interface DeleteModalProps {
    task: CronTaskResponse;
    onClose: () => void;
    onDeleted: () => void;
    showToast: (msg: string, type?: ToastType) => void;
}

function DeleteConfirmModal({ task, onClose, onDeleted, showToast }: DeleteModalProps) {
    const [submitting, setSubmitting] = useState(false);

    const handleDelete = async () => {
        setSubmitting(true);
        try {
            await deleteCronTask(task.job_id);
            showToast('调度任务已删除');
            onDeleted();
        } catch {
            showToast('删除失败', 'error');
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="sch-overlay" onClick={onClose}>
            <div className="sch-modal sch-modal-sm glass-panel" onClick={e => e.stopPropagation()}>
                <div className="sch-modal-header">
                    <h3><Trash2 size={16} /> 确认删除</h3>
                    <button className="sch-modal-close" onClick={onClose}><X size={17} /></button>
                </div>
                <div className="sch-modal-body">
                    <p className="sch-confirm-text">
                        确定删除调度 <strong>{task.spider_name || task.job_id}</strong>{task.description ? `（${task.description}）` : ''} 吗？此操作不可撤销。
                    </p>
                </div>
                <div className="sch-modal-footer">
                    <button className="sch-btn sch-btn-ghost" onClick={onClose}>取消</button>
                    <button className="sch-btn sch-btn-danger" onClick={handleDelete} disabled={submitting}>
                        {submitting ? '删除中...' : '确认删除'}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─────────────────────────────────────────────────
// 主视图
// ─────────────────────────────────────────────────
export default function Scheduler() {
    const navigate = useNavigate();
    const [tasks, setTasks] = useState<CronTaskResponse[]>([]);
    const [projects, setProjects] = useState<ProjectItem[]>([]);
    const [spiders, setSpiders] = useState<SpiderItem[]>([]);
    const [nodes, setNodes] = useState<SpiderNode[]>([]);
    const [loading, setLoading] = useState(true);

    // 弹层状态
    const [modalState, setModalState] = useState<{ visible: boolean; mode: ModalMode; target: CronTaskResponse | null }>({
        visible: false, mode: 'create', target: null,
    });
    const [deleteTarget, setDeleteTarget] = useState<CronTaskResponse | null>(null);
    const [togglingJobs, setTogglingJobs] = useState<Set<string>>(new Set());

    const { toasts, show: showToast } = useToast();

    const loadTasks = useCallback(async () => {
        try {
            setLoading(true);
            const res = await fetchCronTasks();
            setTasks(Array.isArray(res) ? res : []);
        } catch (e) {
            console.error('Failed to load cron tasks', e);
        } finally {
            setLoading(false);
        }
    }, []);

    const loadSpiders = useCallback(async () => {
        try {
            const res = await fetchSpiderList();
            if (res.code === 200 && res.data) setSpiders(res.data);
        } catch { /* silent */ }
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
        loadTasks();
        loadSpiders();
        loadNodes();
        loadProjects();
    }, [loadTasks, loadSpiders, loadNodes, loadProjects]);

    const handleToggle = async (task: CronTaskResponse, newEnabled: boolean) => {
        setTogglingJobs(prev => new Set(prev).add(task.job_id));
        // 乐观更新
        setTasks(prev => prev.map(t => t.job_id === task.job_id ? { ...t, enabled: newEnabled } : t));
        try {
            await toggleCronTask(task.job_id, newEnabled);
            showToast(newEnabled ? '调度已启用' : '调度已暂停', 'info');
            // 重新加载以获取最新的 next_run_time
            loadTasks();
        } catch {
            // 回滚
            setTasks(prev => prev.map(t => t.job_id === task.job_id ? { ...t, enabled: !newEnabled } : t));
            showToast('操作失败', 'error');
        } finally {
            setTogglingJobs(prev => {
                const next = new Set(prev);
                next.delete(task.job_id);
                return next;
            });
        }
    };

    const openCreate = () => {
        loadSpiders();
        loadNodes();
        loadProjects();
        setModalState({ visible: true, mode: 'create', target: null });
    };

    const openEdit = (task: CronTaskResponse) => {
        loadSpiders();
        loadNodes();
        loadProjects();
        setModalState({ visible: true, mode: 'edit', target: task });
    };

    const handleSaved = () => {
        setModalState({ visible: false, mode: 'create', target: null });
        loadTasks();
    };

    const handleDeleted = () => {
        setDeleteTarget(null);
        loadTasks();
    };

    return (
        <div className="sch-container">
            {/* 顶栏 */}
            <div className="sch-header glass-panel">
                <div className="sch-header-left">
                    <h2><CalendarClock size={20} /> 定时调度中心</h2>
                    <span className="sch-count">{tasks.length} 个调度</span>
                </div>
                <button className="sch-btn sch-btn-primary" onClick={openCreate}>
                    <Plus size={15} /> 新增调度
                </button>
            </div>

            {/* 列表 */}
            <div className="sch-table-wrap glass-panel">
                {loading && tasks.length === 0 ? (
                    <div className="sch-empty">
                        <Loader2 size={32} className="spin" />
                        <p>加载中...</p>
                    </div>
                ) : tasks.length === 0 ? (
                    <div className="sch-empty">
                        <CalendarClock size={48} strokeWidth={1} />
                        <p>暂无调度任务，点击右上角「新增调度」添加</p>
                    </div>
                ) : (
                    <table className="sch-table">
                        <thead>
                            <tr>
                                <th>关联爬虫</th>
                                <th>Cron 表达式</th>
                                <th>说明描述</th>
                                <th>目标节点</th>
                                <th>下次执行时间</th>
                                <th style={{ textAlign: 'center' }}>状态</th>
                                <th style={{ width: 100, textAlign: 'right' }}>操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {tasks.map(task => (
                                <tr key={task.job_id} className={!task.enabled ? 'row-disabled' : ''}>
                                    <td>
                                        <div className="sch-spider-cell"
                                            onClick={(e) => { e.stopPropagation(); navigate(`/spiders?id=${task.spider_id}`); }}
                                            style={{ cursor: 'pointer', color: 'var(--accent-primary)' }}
                                            title="跳转到该爬虫"
                                        >
                                            <Bug size={14} className="sch-spider-icon" />
                                            <span>{task.spider_name || `Spider #${task.spider_id}`}</span>
                                        </div>
                                    </td>
                                    <td><span className="sch-cron-tag">{task.cron_expr}</span></td>
                                    <td className="sch-desc-cell" title={task.description || getCronDescription(task.cron_expr)}>
                                        {task.description || <span className="text-muted">{getCronDescription(task.cron_expr) || '—'}</span>}
                                    </td>
                                    <td className="sch-nodes-cell">
                                        {task.target_node_ids && task.target_node_ids.length > 0 ? (
                                            <div className="sch-node-tags">
                                                {task.target_node_ids.map(nid => {
                                                    const node = nodes.find(n => n.node_id === nid);
                                                    return (
                                                        <span key={nid} className="sch-node-tag" title={node ? `${node.name || nid} (${node.status})` : nid}>
                                                            <Server size={12} />
                                                            {node?.name || nid.substring(0, 8)}
                                                            {node && (
                                                                <span
                                                                    className="sch-node-dot"
                                                                    style={{
                                                                        background: node.status === 'online' ? '#4ade80' : '#f87171',
                                                                        marginLeft: '4px'
                                                                    }}
                                                                />
                                                            )}
                                                        </span>
                                                    );
                                                })}
                                            </div>
                                        ) : (
                                            <span className="sch-node-tag public" title="公共队列">
                                                <Server size={12} /> 公共队列
                                            </span>
                                        )}
                                    </td>
                                    <td className="sch-time-cell">
                                        {task.enabled ? (
                                            <span className="sch-next-time">
                                                <Clock size={13} />
                                                {formatDateTime(task.next_run_time)}
                                            </span>
                                        ) : (
                                            <span className="text-muted">已暂停</span>
                                        )}
                                    </td>
                                    <td style={{ textAlign: 'center' }}>
                                        <Switch
                                            checked={task.enabled}
                                            loading={togglingJobs.has(task.job_id)}
                                            onChange={(val) => handleToggle(task, val)}
                                        />
                                    </td>
                                    <td className="sch-actions-cell">
                                        <button className="sch-icon-btn" onClick={() => openEdit(task)} title="编辑">
                                            <Pencil size={15} />
                                        </button>
                                        <button className="sch-icon-btn delete" onClick={() => setDeleteTarget(task)} title="删除">
                                            <Trash2 size={15} />
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* 模态框 */}
            {modalState.visible && (
                <ScheduleModal
                    mode={modalState.mode}
                    initial={modalState.target}
                    projects={projects}
                    spiders={spiders}
                    nodes={nodes}
                    tasks={tasks}
                    onClose={() => setModalState({ visible: false, mode: 'create', target: null })}
                    onSaved={handleSaved}
                    showToast={showToast}
                />
            )}

            {deleteTarget && (
                <DeleteConfirmModal
                    task={deleteTarget}
                    onClose={() => setDeleteTarget(null)}
                    onDeleted={handleDeleted}
                    showToast={showToast}
                />
            )}

            {/* Toast */}
            {toasts.length > 0 && (
                <div className="sch-toast-container">
                    {toasts.map(t => (
                        <div key={t.id} className={`sch-toast sch-toast-${t.type}`}>
                            {t.msg}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
