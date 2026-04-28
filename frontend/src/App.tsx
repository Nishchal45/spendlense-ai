import { NavLink, Outlet } from 'react-router-dom';

import { useAuth } from './auth/useAuth';

// Top-level shell. Renders the header (with auth-aware actions and a
// nav for the protected surfaces) and hands the active route over via
// ``<Outlet />``. Login + register share this chrome, which keeps the
// brand visible on the entire surface — including the unauthenticated
// funnel. Nav is rendered only when the user is signed in; the public
// pages don't have anywhere meaningful to navigate to.
export function App() {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-6">
            <h1 className="text-xl font-semibold text-brand-700">SpendLens</h1>
            {user && (
              <nav className="flex items-center gap-4 text-sm">
                <NavTab to="/">Expenses</NavTab>
                <NavTab to="/receipts">Receipts</NavTab>
              </nav>
            )}
          </div>

          {user ? (
            <div className="flex items-center gap-3 text-sm">
              <span className="text-slate-600">{user.email}</span>
              <button
                type="button"
                onClick={logout}
                className="text-slate-500 hover:text-slate-900 underline-offset-2 hover:underline"
              >
                Sign out
              </button>
            </div>
          ) : (
            <span className="text-sm text-slate-500">self-hosted expense tracker</span>
          )}
        </div>
      </header>
      <main className="flex-1 max-w-5xl w-full mx-auto px-6 py-8">
        <Outlet />
      </main>
      <footer className="border-t border-slate-200 py-4 text-center text-xs text-slate-400">
        Phase 7 — Frontend
      </footer>
    </div>
  );
}

function NavTab({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        isActive ? 'text-brand-700 font-medium' : 'text-slate-500 hover:text-slate-900'
      }
    >
      {children}
    </NavLink>
  );
}
