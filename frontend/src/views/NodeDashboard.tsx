import { useEffect, useState } from 'react';
import { Globe, HardDrive, RefreshCw, Layers, Settings, Trash2, X, Zap } from 'lucide-react';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import { fetchNodeList, updateNodeConfig, uninstallNode } from '@/api/node';
import type { SpiderNode } from '@/types/node';
import './NodeDashboard.css';

// Helper component for generic progress bar
const ProgressBar = ({ percent, label }: { percent: number; label?: string }) => {
    const isDanger = percent >= 80;
    return (
        <div className="progress-container">
            <div className="progress-text-row">
                {label && <span className="progress-label">{label}</span>}
                <span className={`progress-percentage ${isDanger ? 'danger-text' : ''}`}>{percent.toFixed(1)}%</span>
            </div>
            <div className="progress-track">
                <div
                    className={`progress-fill ${isDanger ? 'danger' : 'normal'}`}
                    style={{ width: `${Math.min(percent, 100)}%` }}
                />
            </div>
        </div>
    );
};

export default function NodeDashboard() {
    const [nodes, setNodes] = useState<SpiderNode[]>([]);
    const [loading, setLoading] = useState(true);
    const [lastSync, setLastSync] = useState<Date | null>(null);

    // Modal State
    const [isConfigModalOpen, setConfigModalOpen] = useState(false);
    const [editingNode, setEditingNode] = useState<SpiderNode | null>(null);
    const [configForm, setConfigForm] = useState({
        name: '',
        mac_address: '',
        enabled: true,
        max_runners: 1
    });
    const [formError, setFormError] = useState<string | null>(null);

    // Determines if a node is offline based on if the last heartbeat is older than 15 seconds
    const checkIsOffline = (lastHeartbeatISO: string): boolean => {
        try {
            const hbt = parseISO(lastHeartbeatISO).getTime();
            const now = new Date().getTime();
            return (now - hbt) > 15000;
        } catch (e) {
            return true;
        }
    };

    const loadData = async () => {
        try {
            const res = await fetchNodeList();
            if (res.code === 200 && res.data) {
                // Ensure nodes are sorted logically by role
                const sorted = [...res.data].sort((a, _b) => a.role === 'master' ? -1 : 1);
                setNodes(sorted);
                setLastSync(new Date());
            }
        } catch (e) {
            console.error('Failed to fetch node list:', e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        // Initial Fetch
        loadData();
        // 5 Seconds Polling Hook (Task 3.2 logic)
        const timer = setInterval(loadData, 5000);
        return () => clearInterval(timer);
    }, []);

    const formatRelativeTime = (isoString: string) => {
        try {
            return formatDistanceToNow(parseISO(isoString), { addSuffix: true, locale: zhCN });
        } catch {
            return '未知';
        }
    };

    const handleOpenConfig = (node: SpiderNode) => {
        setEditingNode(node);
        setFormError(null);
        setConfigForm({
            name: node.name || '',
            mac_address: node.mac_address || '',
            enabled: node.enabled,
            max_runners: node.max_runners
        });
        setConfigModalOpen(true);
    };

    const handleSaveConfig = async () => {
        if (!editingNode) return;
        if (configForm.max_runners < 1 || configForm.max_runners > 80) {
            setFormError('并发任务数必须在 1 ~ 80 之间');
            return;
        }
        setFormError(null);
        try {
            await updateNodeConfig(editingNode.node_id, configForm);
            setConfigModalOpen(false);
            loadData();
        } catch (e) {
            console.error('Failed to update config:', e);
            setFormError('保存失败，请稍后重试');
        }
    };

    const handleDeleteNode = async (nodeId: string) => {
        if (!window.confirm('警告：卸载节点将删除该节点的所有历史配置和心跳记录，确定继续吗？')) return;
        try {
            await uninstallNode(nodeId);
            loadData();
        } catch (e) {
            console.error('Uninstall failed', e);
            alert('删除节点失败');
        }
    };

    return (
        <div className="node-table-view-container">
            <div className="table-header-toolbar">
                <h1 className="page-title"><Layers size={22} /> 集群节点管理</h1>
                <div className="table-actions">
                    <span className="last-sync-tag">
                        最后同步: {lastSync ? lastSync.toLocaleTimeString() : '...'}
                    </span>
                    <button className="btn-refresh" onClick={() => { setLoading(true); loadData(); }}>
                        <RefreshCw size={16} className={loading ? 'spin' : ''} />
                    </button>
                </div>
            </div>

            <div className="table-card">
                <table className="spider-table">
                    <thead>
                        <tr>
                            <th style={{ width: '80px' }}>状态</th>
                            <th style={{ width: '150px' }}>节点特征</th>
                            <th style={{ width: '90px', textAlign: 'center' }}>调度</th>
                            <th style={{ width: '90px', textAlign: 'center' }}>并发上限</th>
                            <th style={{ width: '180px' }}>CPU 负载</th>
                            <th style={{ width: '180px' }}>内存使用率</th>
                            <th style={{ width: '250px' }}>系统详情</th>
                            <th>最后心跳</th>
                            <th style={{ width: '120px', textAlign: 'center' }}>操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {nodes.length === 0 && !loading && (
                            <tr><td colSpan={8} style={{ textAlign: "center", padding: "40px" }}>目前没有接入的节点。</td></tr>
                        )}
                        {nodes.map(node => {
                            const isOffline = checkIsOffline(node.last_heartbeat);
                            const memRatioPercent = node.memory_total_mb > 0 ? (node.memory_used_mb / node.memory_total_mb) * 100 : node.mem_usage;

                            return (
                                <tr key={node.node_id} className={isOffline ? 'row-offline' : ''}>
                                    <td>
                                        <div className="status-cell">
                                            <span className={`status-dot ${isOffline ? 'offline' : 'online'}`}></span>
                                            <span className="status-text">{isOffline ? '离线' : '在线'}</span>
                                        </div>
                                    </td>
                                    <td>
                                        <div className="node-id-cell">
                                            <span className="node-title">{node.name || node.node_id}</span>
                                            <div className="role-tags">
                                                <span className={`role-badge ${node.role}`}>{node.role}</span>
                                            </div>
                                        </div>
                                    </td>
                                    <td style={{ textAlign: 'center' }}>
                                        {node.enabled
                                            ? <span className="enabled-badge">已启用</span>
                                            : <span className="disabled-badge">已禁用</span>
                                        }
                                    </td>
                                    <td style={{ textAlign: 'center' }}>
                                        <span className="runners-badge">
                                            <Zap size={12} />
                                            {node.max_runners}
                                        </span>
                                    </td>
                                    <td>
                                        <ProgressBar percent={node.cpu_usage || 0} />
                                    </td>
                                    <td>
                                        <div className="mem-stat-cell">
                                            <div className="mem-stat-row">
                                                <span className="mem-label">
                                                    {node.memory_total_mb
                                                        ? `${Math.round(node.memory_used_mb)} / ${Math.round(node.memory_total_mb)} MB`
                                                        : `${(memRatioPercent || 0).toFixed(1)}%`}
                                                </span>
                                                <span className={`mem-percent ${(memRatioPercent || 0) >= 80 ? 'danger-text' : 'normal-text'}`}>
                                                    {(memRatioPercent || 0).toFixed(1)}%
                                                </span>
                                            </div>
                                            <ProgressBar percent={memRatioPercent || 0} />
                                        </div>
                                    </td>
                                    <td>
                                        <div className="detail-tags-cell">
                                            <span className="info-tag"><Globe size={14} /> {node.ip}</span>
                                            <span className="info-tag"><HardDrive size={14} /> Disk: {node.disk_usage.toFixed(1)}%</span>
                                        </div>
                                    </td>
                                    <td>
                                        <span className={`heartbeat-time ${isOffline ? 'text-danger' : 'text-muted'}`}>
                                            {formatRelativeTime(node.last_heartbeat)}
                                        </span>
                                    </td>
                                    <td>
                                        <div className="action-buttons">
                                            <button className="btn-icon text-primary" onClick={() => handleOpenConfig(node)} title="配置节点">
                                                <Settings size={18} />
                                            </button>
                                            <button className="btn-icon text-danger" onClick={() => handleDeleteNode(node.node_id)} title="卸载节点">
                                                <Trash2 size={18} />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            {/* Configuration Modal */}
            {isConfigModalOpen && (
                <div className="modal-overlay">
                    <div className="modal-content">
                        <div className="modal-header">
                            <h3>节点高级设置</h3>
                            <button className="btn-close" onClick={() => setConfigModalOpen(false)}>
                                <X size={20} />
                            </button>
                        </div>
                        <div className="modal-body form-group-stack">
                            <div className="form-item">
                                <label>节点自定义名称</label>
                                <input
                                    type="text"
                                    className="dark-input"
                                    placeholder="e.g 广州机房Worker_01"
                                    value={configForm.name}
                                    onChange={e => setConfigForm({ ...configForm, name: e.target.value })}
                                />
                            </div>
                            <div className="form-item">
                                <label>MAC 地址 (管理用标识)</label>
                                <input
                                    type="text"
                                    className="dark-input"
                                    placeholder="00:00:00:00:00"
                                    value={configForm.mac_address}
                                    onChange={e => setConfigForm({ ...configForm, mac_address: e.target.value })}
                                />
                            </div>
                            <div className="form-item row-space-between">
                                <label>是否分配爬虫任务 <br /><small className="text-muted">禁用后此节点仅发送心跳</small></label>
                                <label className="switch">
                                    <input
                                        type="checkbox"
                                        checked={configForm.enabled}
                                        onChange={e => setConfigForm({ ...configForm, enabled: e.target.checked })}
                                    />
                                    <span className="slider round"></span>
                                </label>
                            </div>
                            <div className="form-item">
                                <label>最大并发运行任务数 (Max Runners) <small className="text-muted">· 最大 80</small></label>
                                <input
                                    type="number"
                                    className={`dark-input${formError ? ' input-error' : ''}`}
                                    min="1" max="80"
                                    value={configForm.max_runners}
                                    onChange={e => {
                                        const v = parseInt(e.target.value) || 1;
                                        const clamped = Math.min(80, Math.max(1, v));
                                        setFormError(clamped !== v ? '并发任务数必须在 1 ~ 80 之间' : null);
                                        setConfigForm({ ...configForm, max_runners: clamped });
                                    }}
                                />
                                {formError && (
                                    <span className="form-error-text">{formError}</span>
                                )}
                            </div>
                        </div>
                        <div className="modal-footer">
                            <button className="btn-cancel" onClick={() => setConfigModalOpen(false)}>取消</button>
                            <button className="btn-primary" onClick={handleSaveConfig}>应用保存</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
