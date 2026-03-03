import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import Layout from '@/components/Layout';
import Dashboard from '@/views/Dashboard';
import NodeDashboard from '@/views/NodeDashboard';
import TaskManagement from '@/views/TaskManagement';
import Scheduler from '@/views/Scheduler';
import SpidersView from '@/views/SpidersView';
import ProjectManagementView from '@/views/ProjectManagementView';
import Login from '@/views/Login';

const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  const token = localStorage.getItem('token');
  const location = useLocation();

  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <>{children}</>;
};

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="projects" element={<ProjectManagementView />} />
          <Route path="nodes" element={<NodeDashboard />} />
          <Route path="spiders" element={<SpidersView />} />
          <Route path="tasks" element={<TaskManagement />} />
          <Route path="schedules" element={<Scheduler />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
