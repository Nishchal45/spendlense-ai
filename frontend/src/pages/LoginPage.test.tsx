import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { _resetAuthStore, getAuthToken } from '@/auth/authStore';
import { LoginPage } from './LoginPage';

const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

afterEach(() => {
  fetchMock.mockReset();
  _resetAuthStore();
});

function renderLogin(initialPath = '/login') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<p>dashboard</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('LoginPage', () => {
  it('disables submit until both fields are valid', async () => {
    renderLogin();
    const submit = screen.getByRole('button', { name: /sign in/i });
    expect(submit).toBeDisabled();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/email/i), 'me@example.com');
    expect(submit).toBeDisabled(); // password still missing

    await user.type(screen.getByLabelText(/password/i), 'shortpw'); // 7 chars
    expect(submit).toBeDisabled(); // below 8-char floor

    await user.type(screen.getByLabelText(/password/i), '8'); // now 8
    expect(submit).toBeEnabled();
  });

  it('stores the token + redirects on success', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ access_token: 'tk123', token_type: 'bearer', expires_in: 3600 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    renderLogin();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/email/i), 'me@example.com');
    await user.type(screen.getByLabelText(/password/i), 'hunter2hunter2');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(getAuthToken()).toBe('tk123');
    });
    await waitFor(() => {
      expect(screen.getByText('dashboard')).toBeInTheDocument();
    });
  });

  it('shows a friendly message on 401', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Invalid credentials' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    renderLogin();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/email/i), 'me@example.com');
    await user.type(screen.getByLabelText(/password/i), 'hunter2hunter2');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/invalid email or password/i);
    });
    expect(getAuthToken()).toBeNull();
  });
});
