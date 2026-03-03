import { useEffect, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import {
    Activity, Box, Zap, AlertTriangle,
    CheckCircle, XCircle, Clock
} from 'lucide-react';
import {
    fetchDashboardStats,
    fetchTaskTrends,
    fetchRecentTasks,
    type DashboardStats,
    type TrendData,
    type RecentTask
} from '@/api/dashboard';
import './Dashboard.css';

export default function Dashboard() {
    const [stats, setStats] = useState<DashboardStats | null>(null);
    const [trendData, setTrendData] = useState<TrendData[]>([]);
    const [recentTasks, setRecentTasks] = useState<RecentTask[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const loadData = async () => {
            try {
                const [statsRes, trendsRes, recentRes] = await Promise.all([
                    fetchDashboardStats(),
                    fetchTaskTrends(),
                    fetchRecentTasks()
                ]);

                if (statsRes.code === 200) setStats(statsRes.data as DashboardStats);
                if (trendsRes.code === 200) setTrendData(trendsRes.data as TrendData[]);
                if (recentRes.code === 200) setRecentTasks(recentRes.data as RecentTask[]);
            } catch (err) {
                console.error("Failed to fetch dashboard data:", err);
            } finally {
                setLoading(false);
            }
        };

        loadData();
    }, []);

    const chartOptions = {
        title: {
            text: '任务运行趋势 (近 7 天)',
            textStyle: { color: 'var(--text-primary)', fontSize: 16, fontWeight: 500 }
        },
        tooltip: {
            trigger: 'axis'
        },
        legend: {
            data: ['成功任务', '失败任务'],
            textStyle: { color: 'var(--text-secondary)' },
            top: 'top',
            right: 0
        },
        grid: {
            left: '3%',
            right: '4%',
            bottom: '3%',
            containLabel: true
        },
        xAxis: {
            type: 'category',
            boundaryGap: false,
            data: trendData.map(d => d.date),
            axisLabel: { color: 'var(--text-secondary)' },
            axisLine: { lineStyle: { color: 'var(--border-color)' } }
        },
        yAxis: {
            type: 'value',
            axisLabel: { color: 'var(--text-secondary)' },
            splitLine: { lineStyle: { color: 'var(--border-color)', type: 'dashed' } }
        },
        series: [
            {
                name: '成功任务',
                type: 'line',
                smooth: true,
                symbolSize: 8,
                itemStyle: { color: 'var(--status-online)' },
                lineStyle: { width: 3 },
                areaStyle: {
                    color: {
                        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: 'rgba(16, 185, 129, 0.4)' },
                            { offset: 1, color: 'rgba(16, 185, 129, 0.0)' }
                        ]
                    }
                },
                data: trendData.map(d => d.success)
            },
            {
                name: '失败任务',
                type: 'line',
                smooth: true,
                symbolSize: 8,
                itemStyle: { color: 'var(--status-offline)' },
                lineStyle: { width: 3 },
                areaStyle: {
                    color: {
                        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: 'rgba(239, 68, 68, 0.4)' },
                            { offset: 1, color: 'rgba(239, 68, 68, 0.0)' }
                        ]
                    }
                },
                data: trendData.map(d => d.failure)
            }
        ]
    };

    if (loading) {
        return <div className="dashboard-loading">正在加载仪表盘数据...</div>;
    }

    return (
        <div className="dashboard-container">
            {/* 顶层核心指标卡片 */}
            <div className="metric-cards">
                <div className="metric-card glass-panel">
                    <div className="mc-icon" style={{ backgroundColor: 'rgba(16, 185, 129, 0.15)', color: 'var(--status-online)' }}>
                        <Activity size={24} />
                    </div>
                    <div className="mc-content">
                        <p className="mc-label">在线节点</p>
                        <h3 className="mc-value">
                            {stats?.onlineNodes} <span className="mc-subtext">/ {stats?.totalNodes}</span>
                        </h3>
                    </div>
                </div>

                <div className="metric-card glass-panel">
                    <div className="mc-icon" style={{ backgroundColor: 'rgba(59, 130, 246, 0.15)', color: 'var(--accent-primary)' }}>
                        <Box size={24} />
                    </div>
                    <div className="mc-content">
                        <p className="mc-label">总爬虫数</p>
                        <h3 className="mc-value">{stats?.totalSpiders}</h3>
                    </div>
                </div>

                <div className="metric-card glass-panel">
                    <div className="mc-icon" style={{ backgroundColor: 'rgba(245, 158, 11, 0.15)', color: 'var(--status-busy)' }}>
                        <Zap size={24} />
                    </div>
                    <div className="mc-content">
                        <p className="mc-label">今日运行任务</p>
                        <h3 className="mc-value">{stats?.tasksToday}</h3>
                    </div>
                </div>

                <div className="metric-card glass-panel">
                    <div className="mc-icon" style={{ backgroundColor: 'rgba(239, 68, 68, 0.15)', color: 'var(--status-offline)' }}>
                        <AlertTriangle size={24} />
                    </div>
                    <div className="mc-content">
                        <p className="mc-label">今日失败任务</p>
                        <h3 className="mc-value">{stats?.failedTasksToday}</h3>
                    </div>
                </div>
            </div>

            <div className="dashboard-main">
                {/* 趋势图 */}
                <div className="dashboard-chart glass-panel">
                    <ReactECharts
                        option={chartOptions}
                        style={{ height: '100%', width: '100%' }}
                        opts={{ renderer: 'svg' }}
                    />
                </div>

                {/* 最近活动 */}
                <div className="dashboard-feed glass-panel">
                    <h3 className="feed-title">最近运行任务</h3>
                    <div className="feed-list">
                        {recentTasks.map(task => (
                            <div className="feed-item" key={task.id}>
                                <div className="fi-status">
                                    {task.status === 'success' && <CheckCircle size={18} color="var(--status-online)" />}
                                    {task.status === 'failed' && <XCircle size={18} color="var(--status-offline)" />}
                                    {task.status === 'running' && <span className="spin"><Activity size={18} color="var(--accent-primary)" /></span>}
                                </div>
                                <div className="fi-details">
                                    <div className="fi-header">
                                        <span className="fi-spider">{task.spiderName}</span>
                                        <span className={`fi-badge ${task.status}`}>{task.status}</span>
                                    </div>
                                    <div className="fi-meta">
                                        <span className="fi-node">
                                            <Box size={12} /> {task.nodeName}
                                        </span>
                                        <span className="fi-time">
                                            <Clock size={12} /> {task.startTime}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
