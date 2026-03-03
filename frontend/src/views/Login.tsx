import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { login } from '../api/auth';
import { LogIn, Lock, User } from 'lucide-react';
import './Login.css';

const Login: React.FC = () => {
    const navigate = useNavigate();
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [errorMsg, setErrorMsg] = useState('');
    const [isLoading, setIsLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setErrorMsg('');
        setIsLoading(true);

        try {
            const res = await login({ username, password });
            if (res.access_token) {
                localStorage.setItem('token', res.access_token);
                navigate('/dashboard', { replace: true });
            } else {
                setErrorMsg('登录失败：未知返回格式');
            }
        } catch (err: any) {
            setErrorMsg('用户名或密码错误，请重试');
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="login-container">
            <div className="login-background">
                <div className="login-spotlight spot-1"></div>
                <div className="login-spotlight spot-2"></div>
            </div>

            <div className="login-card glass-panel">
                <div className="login-header">
                    <div className="login-logo-wrapper">
                        <LogIn className="login-logo" size={32} />
                    </div>
                    <h2>SpiderManager</h2>
                    <p>分布式爬虫管理系统</p>
                </div>

                <form className="login-form" onSubmit={handleSubmit}>
                    <div className="login-input-group">
                        <label>用户名</label>
                        <div className="login-input-wrapper">
                            <User className="input-icon" size={18} />
                            <input
                                type="text"
                                value={username}
                                onChange={(e) => setUsername(e.target.value)}
                                placeholder="请输入用户名 (admin@example.com)"
                                required
                                disabled={isLoading}
                            />
                        </div>
                    </div>

                    <div className="login-input-group">
                        <label>密码</label>
                        <div className="login-input-wrapper">
                            <Lock className="input-icon" size={18} />
                            <input
                                type="password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                placeholder="请输入密码"
                                required
                                disabled={isLoading}
                            />
                        </div>
                    </div>

                    {errorMsg && <div className="login-error-msg">{errorMsg}</div>}

                    <button
                        type="submit"
                        className={`login-submit-btn ${isLoading ? 'loading' : ''}`}
                        disabled={isLoading}
                    >
                        {isLoading ? '登录中...' : '登录'}
                    </button>
                </form>
            </div>
        </div>
    );
};

export default Login;
