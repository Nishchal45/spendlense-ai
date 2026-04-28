import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createBrowserRouter } from 'react-router-dom';

import { App } from './App';
import { AuthProvider } from './auth/AuthContext';
import { ProtectedRoute } from './auth/ProtectedRoute';
import { ExpensesPage } from './pages/ExpensesPage';
import { LoginPage } from './pages/LoginPage';
import { RegisterPage } from './pages/RegisterPage';
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

// Routing tree:
//
//   /                  ← App shell, public (so login / register
//                        get the branded chrome)
//     /login           ← public
//     /register        ← public
//     [ProtectedRoute] ← auth gate
//       /              ← dashboard (HealthPage placeholder)
//
// As more authed surfaces land they hang off the same protected
// branch — one place to add a route, one place to enforce the gate.
const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { path: 'login', element: <LoginPage /> },
      { path: 'register', element: <RegisterPage /> },
      {
        element: <ProtectedRoute />,
        children: [{ index: true, element: <ExpensesPage /> }],
      },
    ],
  },
]);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
