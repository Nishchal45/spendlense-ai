import { useQuery } from '@tanstack/react-query';

import { apiFetch } from './client';

// Health probe shape from ``GET /api/v1/health``. The backend already
// returns this exact JSON (see ``app/schemas/health.py``); we mirror
// the shape locally so the typecheck catches drift.
export interface HealthResponse {
  status: string;
  version: string;
  environment: string;
}

export function useHealth() {
  return useQuery<HealthResponse>({
    queryKey: ['health'],
    queryFn: () => apiFetch<HealthResponse>('/health'),
    // Health is cheap and changing — show fresh state every minute.
    staleTime: 60_000,
  });
}
