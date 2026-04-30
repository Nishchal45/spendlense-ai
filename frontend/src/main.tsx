import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createBrowserRouter } from 'react-router-dom';

import { App } from './App';
import { AuthProvider } from './auth/AuthContext';
import { ProtectedRoute } from './auth/ProtectedRoute';
import { ExpensesPage } from './pages/ExpensesPage';
import { InsightsPage } from './pages/InsightsPage';
import { LoginPage } from './pages/LoginPage';
import { ReceiptsPage } from './pages/ReceiptsPage';
import { RegisterPage } from './pages/RegisterPage';
import { ShareTargetPage } from './pages/ShareTargetPage';
import './index.css';

// Register the share-target service worker. The SW is the only way a
// static SPA can intercept the manifest's POST to ``/share-target``;
// see ``public/sw.js`` for what it actually does. Browsers without
// SW support (none we target, but defensive) silently skip — every
// other surface still works.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/sw.js').catch((err) => {
      // Logging at warn so a SW bug shows up in the console without
      // breaking the page.
      console.warn('Service worker registration failed', err);
    });
  });
}

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
        children: [
          { index: true, element: <ExpensesPage /> },
          { path: 'receipts', element: <ReceiptsPage /> },
          { path: 'insights', element: <InsightsPage /> },
          { path: 'share-target', element: <ShareTargetPage /> },
        ],
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
