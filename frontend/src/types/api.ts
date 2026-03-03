export interface ApiResponse<T = any> {
  code: number;
  data: T;
  message: string;
}

export interface PaginatedData<T> {
  total: number;
  items: T[];
  page: number;
  size: number;
}
