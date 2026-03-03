export enum NodeStatus {
    ONLINE = 'online',
    OFFLINE = 'offline',
    BUSY = 'busy',
}

export interface SpiderNode {
    node_id: string;
    name?: string;
    role: 'master' | 'worker';
    ip: string;
    status: NodeStatus;
    cpu_usage: number;
    mem_usage: number;
    disk_usage: number;
    memory_total_mb: number;
    memory_used_mb: number;
    last_heartbeat: string;
    mac_address?: string;
    enabled: boolean;
    max_runners: number;
}

export interface NodeConfigUpdate {
    name: string;
    mac_address: string;
    enabled: boolean;
    max_runners: number;
}
