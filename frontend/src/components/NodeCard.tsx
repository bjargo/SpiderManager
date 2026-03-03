import React, { useState } from 'react';
import type { SpiderNode } from '@/types/node';
import { Cpu, MemoryStick, Clock, HardDrive, Edit2, Check, X, Globe } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import { updateNodeConfig } from '@/api/node';
import './NodeCard.css';

interface Props {
    node: SpiderNode;
    onUpdate?: () => void;
}

const NodeCard: React.FC<Props> = ({ node, onUpdate }) => {
    const [isEditing, setIsEditing] = useState(false);
    const [tempName, setTempName] = useState(node.name || '');

    const getStatusColor = (status: string) => {
        if (status === 'online') return 'var(--status-online)';
        if (status === 'busy') return 'var(--status-busy)';
        return 'var(--status-offline)';
    };

    const handleSaveName = async () => {
        if (!tempName.trim() || tempName === node.name) {
            setIsEditing(false);
            return;
        }
        try {
            await updateNodeConfig(node.node_id, { name: tempName });
            setIsEditing(false);
            onUpdate?.();
        } catch (e) {
            console.error('Failed to update node name:', e);
        }
    };

    const timeAgo = formatDistanceToNow(new Date(node.last_heartbeat), { addSuffix: true, locale: zhCN });

    return (
        <div className="node-card glass-panel">
            <div className="nc-header">
                <div className="nc-title-area">
                    {isEditing ? (
                        <div className="edit-box">
                            <input
                                className="name-input"
                                value={tempName}
                                onChange={(e) => setTempName(e.target.value)}
                                autoFocus
                            />
                            <Check size={14} className="edit-confirm" onClick={handleSaveName} />
                            <X size={14} className="edit-cancel" onClick={() => setIsEditing(false)} />
                        </div>
                    ) : (
                        <div className="title-box">
                            <h3 className="nc-title">{node.name || node.node_id}</h3>
                            <button className="edit-btn" onClick={() => setIsEditing(true)}>
                                <Edit2 size={12} />
                            </button>
                        </div>
                    )}
                    <span className={`role-badge role-${node.role}`}>{node.role}</span>
                </div>
                <div className="nc-status">
                    <span className="status-dot" style={{ backgroundColor: getStatusColor(node.status) }} />
                    <span className="status-text">{node.status.toUpperCase()}</span>
                </div>
            </div>

            <div className="nc-info-row">
                <Globe size={14} />
                <span className="node-ip">{node.ip}</span>
            </div>

            <div className="nc-metrics">
                <div className="metric">
                    <div className="metric-icon"><Cpu size={16} /></div>
                    <div className="metric-content">
                        <span className="metric-label">CPU</span>
                        <div className="progress-bar">
                            <div className="progress-fill" style={{ width: `${node.cpu_usage}%`, backgroundColor: node.cpu_usage > 80 ? 'var(--status-offline)' : 'var(--accent-primary)' }} />
                        </div>
                        <span className="metric-value">{node.cpu_usage.toFixed(1)}%</span>
                    </div>
                </div>

                <div className="metric">
                    <div className="metric-icon"><MemoryStick size={16} /></div>
                    <div className="metric-content">
                        <span className="metric-label">MEM</span>
                        <div className="progress-bar">
                            <div className="progress-fill" style={{ width: `${node.mem_usage}%`, backgroundColor: node.mem_usage > 80 ? 'var(--status-offline)' : 'var(--accent-primary)' }} />
                        </div>
                        <span className="metric-value">{node.mem_usage.toFixed(1)}%</span>
                    </div>
                </div>

                <div className="metric">
                    <div className="metric-icon"><HardDrive size={16} /></div>
                    <div className="metric-content">
                        <span className="metric-label">DISK</span>
                        <div className="progress-bar">
                            <div className="progress-fill" style={{ width: `${node.disk_usage}%`, backgroundColor: node.disk_usage > 90 ? 'var(--status-offline)' : '#10b981' }} />
                        </div>
                        <span className="metric-value">{node.disk_usage.toFixed(1)}%</span>
                    </div>
                </div>
            </div>

            <div className="nc-footer">
                <div className="footer-left">
                    <Clock size={14} />
                    <span>{timeAgo}</span>
                </div>
                <span className="node-id-fine">ID: {node.node_id}</span>
            </div>
        </div>
    );
};

export default NodeCard;
