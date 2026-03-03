import { useState, useEffect, useRef, useCallback } from 'react';
import { X, Save, FileCode, Loader2, FolderOpen, Plus, Trash2, Check } from 'lucide-react';
import { fetchSpiderFiles, fetchSpiderFileContent, saveSpiderFileContent, createSpiderFile, deleteSpiderFile } from '@/api/spider';
import type { SpiderItem } from '@/types/spider';

// CodeMirror 6
import { EditorView, basicSetup } from 'codemirror';
import { EditorState } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { python } from '@codemirror/lang-python';
import { json } from '@codemirror/lang-json';
import { javascript } from '@codemirror/lang-javascript';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import { yaml } from '@codemirror/lang-yaml';

import './CodeEditorModal.css';

// ── 类型 ──
type ToastType = 'success' | 'error';

interface CodeEditorModalProps {
    spider: SpiderItem;
    onClose: () => void;
    showToast: (msg: string, type?: ToastType) => void;
}

// ── 工具函数 ──
function getFileIcon(filename: string): string {
    const ext = filename.split('.').pop()?.toLowerCase() ?? '';
    const map: Record<string, string> = {
        py: '🐍', json: '📋', js: '📜', ts: '📘',
        html: '🌐', css: '🎨', yaml: '⚙️', yml: '⚙️',
        md: '📝', txt: '📄', sh: '🖥️', bat: '🖥️',
        cfg: '⚙️', ini: '⚙️', toml: '⚙️', xml: '📄',
    };
    return map[ext] ?? '📄';
}

function getLanguageExtension(filename: string) {
    const ext = filename.split('.').pop()?.toLowerCase() ?? '';
    switch (ext) {
        case 'py': return python();
        case 'json': return json();
        case 'js': case 'jsx': case 'ts': case 'tsx': return javascript();
        case 'html': case 'htm': return html();
        case 'css': case 'scss': case 'less': return css();
        case 'yaml': case 'yml': return yaml();
        default: return [];
    }
}

function getLanguageLabel(filename: string): string {
    const ext = filename.split('.').pop()?.toLowerCase() ?? '';
    const labels: Record<string, string> = {
        py: 'Python', json: 'JSON', js: 'JavaScript', ts: 'TypeScript',
        jsx: 'React JSX', tsx: 'React TSX', html: 'HTML', htm: 'HTML',
        css: 'CSS', scss: 'SCSS', yaml: 'YAML', yml: 'YAML',
        md: 'Markdown', txt: 'Plain Text', sh: 'Shell', bat: 'Batch',
        cfg: 'Config', ini: 'INI', toml: 'TOML', xml: 'XML',
    };
    return labels[ext] ?? 'Plain Text';
}

// ── 主组件 ──
export default function CodeEditorModal({ spider, onClose, showToast }: CodeEditorModalProps) {
    const [files, setFiles] = useState<string[]>([]);
    const [filesLoading, setFilesLoading] = useState(true);
    const [selectedFile, setSelectedFile] = useState<string | null>(null);
    const [fileContent, setFileContent] = useState<string>('');
    const [originalContent, setOriginalContent] = useState<string>('');
    const [fileLoading, setFileLoading] = useState(false);
    const [saving, setSaving] = useState(false);

    // 新建文件状态
    const [isCreating, setIsCreating] = useState(false);
    const [newFileName, setNewFileName] = useState('');
    const [createLoading, setCreateLoading] = useState(false);
    const newFileInputRef = useRef<HTMLInputElement>(null);

    // 删除文件状态
    const [deletingFile, setDeletingFile] = useState<string | null>(null);
    const [deleteLoading, setDeleteLoading] = useState(false);

    const editorRef = useRef<HTMLDivElement>(null);
    const viewRef = useRef<EditorView | null>(null);

    const isModified = fileContent !== originalContent;

    // ── 加载文件列表 ──
    useEffect(() => {
        let cancelled = false;
        (async () => {
            setFilesLoading(true);
            try {
                const res = await fetchSpiderFiles(spider.id);
                if (!cancelled && res.code === 200 && res.data) {
                    setFiles(res.data);
                    // 自动选中第一个文件
                    if (res.data.length > 0) {
                        setSelectedFile(res.data[0]);
                    }
                }
            } catch {
                if (!cancelled) showToast('加载文件列表失败', 'error');
            }
            if (!cancelled) setFilesLoading(false);
        })();
        return () => { cancelled = true; };
    }, [spider.id]);

    // ── 加载选中文件内容 ──
    useEffect(() => {
        if (!selectedFile) return;
        let cancelled = false;
        (async () => {
            setFileLoading(true);
            try {
                const res = await fetchSpiderFileContent(spider.id, selectedFile);
                if (!cancelled && res.code === 200 && res.data) {
                    setFileContent(res.data.content);
                    setOriginalContent(res.data.content);
                }
            } catch {
                if (!cancelled) showToast('加载文件内容失败', 'error');
            }
            if (!cancelled) setFileLoading(false);
        })();
        return () => { cancelled = true; };
    }, [spider.id, selectedFile]);

    // ── 初始化/更新 CodeMirror ──
    useEffect(() => {
        if (fileLoading || !editorRef.current || !selectedFile) return;

        // 销毁旧实例
        if (viewRef.current) {
            viewRef.current.destroy();
            viewRef.current = null;
        }

        const langExt = getLanguageExtension(selectedFile);
        const extensions = [
            basicSetup,
            oneDark,
            EditorView.updateListener.of((update) => {
                if (update.docChanged) {
                    const newContent = update.state.doc.toString();
                    setFileContent(newContent);
                }
            }),
            ...(Array.isArray(langExt) ? langExt : [langExt]),
        ];

        const state = EditorState.create({
            doc: fileContent,
            extensions,
        });

        viewRef.current = new EditorView({
            state,
            parent: editorRef.current,
        });

        return () => {
            if (viewRef.current) {
                viewRef.current.destroy();
                viewRef.current = null;
            }
        };
    }, [fileLoading, selectedFile, originalContent]);

    // ── 保存文件 ──
    const handleSave = useCallback(async () => {
        if (!selectedFile || !isModified || saving) return;
        setSaving(true);
        try {
            const res = await saveSpiderFileContent(spider.id, {
                path: selectedFile,
                content: fileContent,
            });
            if (res.code === 200) {
                setOriginalContent(fileContent);
                showToast('文件保存成功', 'success');
            } else {
                showToast(res.message || '保存失败', 'error');
            }
        } catch {
            showToast('保存出现异常', 'error');
        } finally {
            setSaving(false);
        }
    }, [selectedFile, fileContent, isModified, saving, spider.id, showToast]);

    // ── Ctrl+S 快捷键 ──
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                e.preventDefault();
                handleSave();
            }
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [handleSave]);

    // ── 切换文件 ──
    const handleFileClick = (path: string) => {
        if (path === selectedFile) return;
        setSelectedFile(path);
    };

    // ── 新建文件 ──
    const handleStartCreate = () => {
        setIsCreating(true);
        setNewFileName('');
        setTimeout(() => newFileInputRef.current?.focus(), 50);
    };

    const handleCancelCreate = () => {
        setIsCreating(false);
        setNewFileName('');
    };

    const handleConfirmCreate = async () => {
        const trimmed = newFileName.trim();
        if (!trimmed) {
            showToast('文件名不能为空', 'error');
            return;
        }
        if (trimmed.includes('..') || trimmed.startsWith('/')) {
            showToast('文件名不合法', 'error');
            return;
        }

        setCreateLoading(true);
        try {
            const res = await createSpiderFile(spider.id, { path: trimmed, content: '' });
            if (res.code === 200 && res.data) {
                setFiles(res.data);
                setSelectedFile(trimmed);
                showToast('文件创建成功', 'success');
                setIsCreating(false);
                setNewFileName('');
            } else {
                showToast(res.message || '创建失败', 'error');
            }
        } catch {
            showToast('创建文件异常', 'error');
        } finally {
            setCreateLoading(false);
        }
    };

    const handleCreateKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            handleConfirmCreate();
        } else if (e.key === 'Escape') {
            handleCancelCreate();
        }
    };

    // ── 删除文件 ──
    const handleDeleteFile = async (path: string) => {
        setDeleteLoading(true);
        try {
            const res = await deleteSpiderFile(spider.id, { path });
            if (res.code === 200 && res.data) {
                setFiles(res.data);
                // 如果删除的是当前选中的文件，切换到第一个
                if (selectedFile === path) {
                    setSelectedFile(res.data.length > 0 ? res.data[0] : null);
                }
                showToast('文件已删除', 'success');
                setDeletingFile(null);
            } else {
                showToast(res.message || '删除失败', 'error');
            }
        } catch {
            showToast('删除文件异常', 'error');
        } finally {
            setDeleteLoading(false);
        }
    };

    return (
        <div className="ce-overlay" onClick={onClose}>
            <div className="ce-container glass-panel" onClick={e => e.stopPropagation()}>
                {/* 顶部工具栏 */}
                <div className="ce-header">
                    <div className="ce-header-left">
                        <h3><FileCode size={16} /> {spider.name}</h3>
                        {selectedFile && (
                            <span className="ce-file-path">
                                {selectedFile}
                                {isModified && <span className="ce-modified-dot" title="已修改" />}
                            </span>
                        )}
                    </div>
                    <div className="ce-header-right">
                        <button
                            className="ce-btn ce-btn-save"
                            onClick={handleSave}
                            disabled={!isModified || saving}
                        >
                            {saving
                                ? <><Loader2 size={14} className="spin" /> 保存中...</>
                                : <><Save size={14} /> 保存</>
                            }
                        </button>
                        <button className="ce-btn ce-btn-close" onClick={onClose}>
                            <X size={14} /> 关闭
                        </button>
                    </div>
                </div>

                {/* 主体 */}
                <div className="ce-body">
                    {/* 左侧文件树 */}
                    <div className="ce-sidebar">
                        <div className="ce-sidebar-header">
                            <FolderOpen size={13} /> 文件列表
                            <button
                                className="ce-sidebar-add-btn"
                                onClick={handleStartCreate}
                                title="新建文件"
                            >
                                <Plus size={14} />
                            </button>
                        </div>
                        {filesLoading ? (
                            <div className="ce-sidebar-loading">
                                <Loader2 size={20} className="spin" />
                                <span>加载中...</span>
                            </div>
                        ) : files.length === 0 && !isCreating ? (
                            <div className="ce-sidebar-empty">ZIP 包内无可读文件</div>
                        ) : (
                            <div className="ce-file-list">
                                {/* 新建文件输入行 */}
                                {isCreating && (
                                    <div className="ce-file-create-row">
                                        <input
                                            ref={newFileInputRef}
                                            className="ce-file-create-input"
                                            placeholder="输入文件名，如 utils.py"
                                            value={newFileName}
                                            onChange={e => setNewFileName(e.target.value)}
                                            onKeyDown={handleCreateKeyDown}
                                            disabled={createLoading}
                                        />
                                        <button
                                            className="ce-file-create-confirm"
                                            onClick={handleConfirmCreate}
                                            disabled={createLoading || !newFileName.trim()}
                                            title="确认创建"
                                        >
                                            {createLoading
                                                ? <Loader2 size={12} className="spin" />
                                                : <Check size={12} />
                                            }
                                        </button>
                                        <button
                                            className="ce-file-create-cancel"
                                            onClick={handleCancelCreate}
                                            disabled={createLoading}
                                            title="取消"
                                        >
                                            <X size={12} />
                                        </button>
                                    </div>
                                )}

                                {files.map(f => (
                                    <div
                                        key={f}
                                        className={`ce-file-item ${selectedFile === f ? 'active' : ''}`}
                                        onClick={() => handleFileClick(f)}
                                        title={f}
                                    >
                                        <span className="file-icon">{getFileIcon(f)}</span>
                                        <span className="ce-file-name">
                                            {f.includes('/') ? f.split('/').pop() : f}
                                        </span>

                                        {/* 删除确认 */}
                                        {deletingFile === f ? (
                                            <span className="ce-file-delete-confirm" onClick={e => e.stopPropagation()}>
                                                <button
                                                    className="ce-file-delete-yes"
                                                    onClick={() => handleDeleteFile(f)}
                                                    disabled={deleteLoading}
                                                    title="确认删除"
                                                >
                                                    {deleteLoading
                                                        ? <Loader2 size={11} className="spin" />
                                                        : <Check size={11} />
                                                    }
                                                </button>
                                                <button
                                                    className="ce-file-delete-no"
                                                    onClick={() => setDeletingFile(null)}
                                                    disabled={deleteLoading}
                                                    title="取消"
                                                >
                                                    <X size={11} />
                                                </button>
                                            </span>
                                        ) : (
                                            <button
                                                className="ce-file-delete-btn"
                                                onClick={e => { e.stopPropagation(); setDeletingFile(f); }}
                                                title="删除文件"
                                            >
                                                <Trash2 size={12} />
                                            </button>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* 右侧编辑器 */}
                    <div className="ce-editor-area">
                        {!selectedFile ? (
                            <div className="ce-editor-placeholder">
                                <FileCode size={48} />
                                <p>选择左侧文件查看代码</p>
                            </div>
                        ) : fileLoading ? (
                            <div className="ce-editor-loading">
                                <Loader2 size={20} className="spin" />
                                加载文件内容...
                            </div>
                        ) : (
                            <div className="ce-codemirror-wrap" ref={editorRef} />
                        )}
                    </div>
                </div>

                {/* 底部状态栏 */}
                {selectedFile && !fileLoading && (
                    <div className="ce-status-bar">
                        <span>
                            {files.length} 个文件 · {isModified ? '● 已修改' : '✓ 未修改'}
                        </span>
                        <span className="ce-status-lang">{getLanguageLabel(selectedFile)}</span>
                    </div>
                )}
            </div>
        </div>
    );
}
