import { useState, useEffect } from 'react';
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom';
import {
    Activity, Play, CalendarClock, Settings, LayoutDashboard,
    FolderKanban, LogOut, PanelLeftClose, PanelLeftOpen, Sun, Moon, PackageOpen
} from 'lucide-react';
import { format } from 'date-fns';
import './Layout.css';

export default function Layout() {
    const navigate = useNavigate();
    const location = useLocation();

    const [isCollapsed, setIsCollapsed] = useState(false);
    const [currentTime, setCurrentTime] = useState(new Date());
    const [isDark, setIsDark] = useState(() => {
        return localStorage.getItem('theme') !== 'light';
    });

    useEffect(() => {
        const timer = setInterval(() => {
            setCurrentTime(new Date());
        }, 1000);
        return () => clearInterval(timer);
    }, []);

    useEffect(() => {
        if (isDark) {
            document.documentElement.classList.remove('light-theme');
            localStorage.setItem('theme', 'dark');
        } else {
            document.documentElement.classList.add('light-theme');
            localStorage.setItem('theme', 'light');
        }
    }, [isDark]);

    const handleLogout = () => {
        localStorage.removeItem('token');
        navigate('/login', { replace: true });
    };

    const navItems = [
        { name: '概览', path: '/dashboard', icon: <LayoutDashboard size={20} /> },
        { name: '项目管理', path: '/projects', icon: <PackageOpen size={20} /> },
        { name: '节点管理', path: '/nodes', icon: <Activity size={20} /> },
        { name: '爬虫管理', path: '/spiders', icon: <FolderKanban size={20} /> },
        { name: '任务列表', path: '/tasks', icon: <Play size={20} /> },
        { name: '定时调度', path: '/schedules', icon: <CalendarClock size={20} /> },
    ];

    // Simple Breadcrumb logic
    const getBreadcrumbName = (path: string) => {
        const match = navItems.find(item => path.startsWith(item.path));
        return match ? match.name : '页面';
    };

    return (
        <div className={`layout-container ${isDark ? 'theme-dark' : 'theme-light'}`}>
            <aside className={`sidebar ${isCollapsed ? 'collapsed' : ''}`}>
                <div className="sidebar-header">
                    <div className="brand-icon">
                        <Activity strokeWidth={3} size={24} color="var(--accent-primary)" />
                    </div>
                    {!isCollapsed && <h2>LightCrawlab</h2>}
                </div>

                <nav className="nav-menu">
                    {navItems.map((item) => (
                        <NavLink
                            key={item.path}
                            to={item.path}
                            className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
                            title={isCollapsed ? item.name : undefined}
                        >
                            <div className="nav-item-icon">{item.icon}</div>
                            {!isCollapsed && <span className="nav-item-text">{item.name}</span>}
                        </NavLink>
                    ))}
                </nav>

                <div className="sidebar-footer">
                    <button className="collapse-toggle-btn" onClick={() => setIsCollapsed(!isCollapsed)}>
                        {isCollapsed ? <PanelLeftOpen size={20} /> : <PanelLeftClose size={20} />}
                        {!isCollapsed && <span>折叠面板</span>}
                    </button>
                    {!isCollapsed && (
                        <button className="settings-btn" style={{ marginTop: '8px' }}>
                            <Settings size={20} />
                            <span>设置</span>
                        </button>
                    )}
                </div>
            </aside>

            <main className="main-content">
                <header className="topbar">
                    <div className="topbar-left">
                        <div className="breadcrumb">
                            <span className="breadcrumb-path">
                                / {getBreadcrumbName(location.pathname)}
                            </span>
                        </div>
                    </div>

                    <div className="topbar-right">
                        <div className="time-display">
                            {format(currentTime, 'yyyy-MM-dd HH:mm:ss')}
                        </div>

                        <button className="icon-btn theme-toggle" onClick={() => setIsDark(!isDark)} title="切换主题">
                            {isDark ? <Sun size={18} /> : <Moon size={18} />}
                        </button>

                        <div className="user-profile">
                            <div className="avatar">A</div>
                            <span className="username">Admin</span>
                            <button className="logout-btn" onClick={handleLogout} title="退出登录">
                                <LogOut size={18} />
                            </button>
                        </div>
                    </div>
                </header>

                <div className="content-wrapper">
                    <Outlet />
                </div>
            </main>
        </div>
    );
}
