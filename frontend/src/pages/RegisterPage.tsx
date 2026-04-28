import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { useRegister } from '@/api/auth';
import { ApiError } from '@/api/client';

const PASSWORD_MIN_LENGTH = 8;

// Sign-up form. After a successful POST /auth/register the user is
// pushed to /login with the email pre-filled via router state — see
// ``api/auth.ts`` for why we don't auto-session on register.
export function RegisterPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const navigate = useNavigate();
  const register = useRegister();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    register.mutate(
      { email, password },
      {
        onSuccess: () => navigate('/login', { replace: true, state: { prefilledEmail: email } }),
      },
    );
  }

  const errorMessage = formatRegisterError(register.error);
  const submitDisabled = register.isPending || !email || password.length < PASSWORD_MIN_LENGTH;

  return (
    <section className="max-w-sm mx-auto bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
      <h2 className="text-xl font-semibold mb-1">Create an account</h2>
      <p className="text-sm text-slate-500 mb-6">
        Already have one?{' '}
        <Link to="/login" className="text-brand-600 hover:underline">
          Sign in
        </Link>
        .
      </p>

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <label className="block">
          <span className="block text-sm font-medium text-slate-700 mb-1">Email</span>
          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            className="w-full border border-slate-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          />
        </label>
        <label className="block">
          <span className="block text-sm font-medium text-slate-700 mb-1">Password</span>
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            minLength={PASSWORD_MIN_LENGTH}
            required
            className="w-full border border-slate-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          />
          <span className="block text-xs text-slate-500 mt-1">
            At least {PASSWORD_MIN_LENGTH} characters.
          </span>
        </label>

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
          {register.isPending ? 'Creating…' : 'Create account'}
        </button>
      </form>
    </section>
  );
}

function formatRegisterError(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    // Backend returns 409 when the email is already taken.
    if (error.status === 409) return 'An account with that email already exists.';
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string') return detail;
  }
  return 'Sign-up failed. Please try again.';
}
