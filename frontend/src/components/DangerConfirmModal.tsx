/**
 * DangerConfirmModal — 高危操作红色警告弹窗
 *
 * 用于删除等不可逆操作的二次确认，带有醒目的红色警告样式。
 *
 * 用法：
 *   <DangerConfirmModal
 *     open={showModal}
 *     title="删除爬虫"
 *     description={<>确定删除 <strong>{spider.name}</strong> 吗？此操作不可撤销。</>}
 *     confirmText="确认删除"
 *     onConfirm={handleDelete}
 *     onCancel={() => setShowModal(false)}
 *     loading={deleting}
 *   />
 */
import { useEffect } from 'react';
import { AlertTriangle, X, Loader2 } from 'lucide-react';
import type { ReactNode } from 'react';
import './DangerConfirmModal.css';

interface DangerConfirmModalProps {
    /** 是否显示 */
    open: boolean;
    /** 弹窗标题 */
    title?: string;
    /** 警告内容，支持 ReactNode */
    description: ReactNode;
    /** 确认按钮文字，默认"确认删除" */
    confirmText?: string;
    /** 取消按钮文字，默认"取消" */
    cancelText?: string;
    /** 确认回调 */
    onConfirm: () => void;
    /** 取消/关闭回调 */
    onCancel: () => void;
    /** 正在提交中，按钮禁用并显示 loading */
    loading?: boolean;
}

export default function DangerConfirmModal({
    open,
    title = '高危操作确认',
    description,
    confirmText = '确认删除',
    cancelText = '取消',
    onConfirm,
    onCancel,
    loading = false,
}: DangerConfirmModalProps) {
    // ESC 键关闭
    useEffect(() => {
        if (!open) return;
        const handler = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onCancel();
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [open, onCancel]);

    if (!open) return null;

    return (
        <div className="dcm-overlay" onClick={onCancel} role="dialog" aria-modal="true">
            <div className="dcm-modal" onClick={e => e.stopPropagation()}>
                {/* 顶部警告色条 */}
                <div className="dcm-danger-strip" />

                <div className="dcm-content">
                    {/* 图标 + 标题 */}
                    <div className="dcm-header">
                        <div className="dcm-icon-wrap">
                            <AlertTriangle size={22} className="dcm-icon" />
                        </div>
                        <span className="dcm-title">{title}</span>
                        <button className="dcm-close" onClick={onCancel} aria-label="关闭">
                            <X size={17} />
                        </button>
                    </div>

                    {/* 警告内容 */}
                    <div className="dcm-body">
                        <p className="dcm-description">{description}</p>
                        <div className="dcm-warning-badge">
                            <AlertTriangle size={13} />
                            此操作不可撤销，请谨慎确认
                        </div>
                    </div>

                    {/* 操作按钮 */}
                    <div className="dcm-footer">
                        <button className="dcm-btn dcm-btn-cancel" onClick={onCancel} disabled={loading}>
                            {cancelText}
                        </button>
                        <button className="dcm-btn dcm-btn-danger" onClick={onConfirm} disabled={loading}>
                            {loading
                                ? <><Loader2 size={14} className="dcm-spin" /> 处理中...</>
                                : <><AlertTriangle size={14} /> {confirmText}</>
                            }
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
