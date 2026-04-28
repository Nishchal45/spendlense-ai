import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { useLogin } from '@/api/auth';
import { ApiError } from '@/api/client';

interface LocationState {
  from?: { pathname: string };
}

const PASSWORD_MIN_LENGTH = 8;

// Login form. Single-column, controlled inputs, no React Hook Form —
// the surface is small enough that pulling in a form lib would just
// be ceremony. The ``ApiError`` parsing handles the FastAPI-typical
// ``{detail: "..."}`` shape so 401s land as a friendly message
// instead of a stack-trace dump.
export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const navigate = useNavigate();
  const location = useLocation();
  const login = useLogin();

  // After login, send the user back to wherever ``ProtectedRoute``
  // pushed them away from — defaults to the dashboard for fresh
  // arrivals.
  const from = (location.state as LocationState | null)?.from?.pathname ?? '/';

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    login.mutate({ email, password }, { onSuccess: () => navigate(from, { replace: true }) });
  }

  const errorMessage = formatLoginError(login.error);
  const submitDisabled = login.isPending || !email || password.length < PASSWORD_MIN_LENGTH;

  return (
    <section className="max-w-sm mx-auto bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
      <h2 className="text-xl font-semibold mb-1">Sign in</h2>
      <p className="text-sm text-slate-500 mb-6">
        New here?{' '}
        <Link to="/register" className="text-brand-600 hover:underline">
          Create an account
        </Link>
        .
      </p>

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <Field
          label="Email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={setEmail}
          required
        />
        <Field
          label="Password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={setPassword}
          minLength={PASSWORD_MIN_LENGTH}
          required
        />

        {errorMessage && (
          <p role="alert" className="text-sm text-red-600">
            {errorMessage}
          </p>
        )}

        <button
          type="submit"
          disabled={submitDisabled}
          className="w-full bg-brand-600 hover:bg-brand-700 disabled:bg-slate-300 text-white font-medium rounded-md py-2 transition-colors"
        >
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </section>
  );
}

interface FieldProps {
  label: string;
  type: 'email' | 'password' | 'text';
  autoComplete: string;
  value: string;
  onChange: (next: string) => void;
  minLength?: number;
  required?: boolean;
}

function Field({ label, type, autoComplete, value, onChange, minLength, required }: FieldProps) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-slate-700 mb-1">{label}</span>
      <input
        type={type}
        autoComplete={autoComplete}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        minLength={minLength}
        required={required}
        className="w-full border border-slate-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
      />
    </label>
  );
}

function formatLoginError(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Invalid email or password.';
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string') return detail;
  }
  return 'Sign-in failed. Please try again.';
}
