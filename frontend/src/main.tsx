import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createBrowserRouter } from 'react-router-dom';

import { App } from './App';
import { HealthPage } from './pages/HealthPage';
import './index.css';

// Single React Query client for the whole app. Defaults are tuned for
// "humans clicking around an expense dashboard" rather than "background
// job pulling huge lists":
//
// * ``refetchOnWindowFocus: false`` — re-fetching every time the user
//   alt-tabs is annoying for a finance UI.
// * ``staleTime: 30_000`` — half a minute of cache is plenty for
//   "what's my expense list look like"; receipt status polling
//   overrides this per-query.
// * ``retry: 1`` — one auto-retry covers transient network blips
//   without amplifying a real outage.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 30_000,
      retry: 1,
    },
  },
});

// Routes are declared centrally here. As the app grows we'll split
// this into a ``routes.tsx`` module, but a flat object is more
// readable while there's only one route.
const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [{ index: true, element: <HealthPage /> }],
  },
]);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
