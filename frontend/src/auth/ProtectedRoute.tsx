import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { useAuth } from './useAuth';

// Route guard. Wraps a subtree that requires a logged-in user.
//
// Three cases:
//
// 1. **No token** → redirect to ``/login``, remembering where the user
//    came from in router state so we can return them after auth.
// 2. **Token but ``/auth/me`` still loading** → render a soft loading
//    state. Showing the protected children before we've validated
//    the token would briefly leak data on every page load.
// 3. **Token and user resolved** → render the children via ``<Outlet />``.
//
// The ``/auth/me`` call has its own 401 handling (the API client
// clears the token), so a stale token quietly degrades to "no token"
// → redirect, without us having to special-case it here.
export function ProtectedRoute() {
  const { token, user, isLoading } = useAuth();
  const location = useLocation();

  if (!token) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (isLoading || !user) {
    return (
      <div className="flex items-center justify-center min-h-[40vh]">
        <p className="text-slate-500">Loading…</p>
      </div>
    );
  }

  return <Outlet />;
}
