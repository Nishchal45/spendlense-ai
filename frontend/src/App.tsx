import { Outlet } from 'react-router-dom';

// Top-level shell. Holds the persistent chrome (header, eventual nav)
// and renders the active route via ``<Outlet />``. Auth context and
// the protected-route wrapper land in PR #B; for now this is just a
// branded frame so the scaffold has something to look at.
export function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
          <h1 className="text-xl font-semibold text-brand-700">SpendLens</h1>
          <span className="text-sm text-slate-500">self-hosted expense tracker</span>
        </div>
      </header>
      <main className="flex-1 max-w-5xl w-full mx-auto px-6 py-8">
        <Outlet />
      </main>
      <footer className="border-t border-slate-200 py-4 text-center text-xs text-slate-400">
        Phase 7 — Frontend scaffold
      </footer>
    </div>
  );
}
