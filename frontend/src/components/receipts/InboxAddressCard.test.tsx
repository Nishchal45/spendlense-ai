import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { AuthContext, type AuthContextValue } from '@/auth/AuthContext';
import { InboxAddressCard } from './InboxAddressCard';

// Provider stub injects a known user without spinning up TanStack
// Query — keeps the test focused on the card's render + clipboard
// behaviour, not on auth wiring.
function withUser(value: AuthContextValue): {
  wrapper: (props: { children: ReactNode }) => JSX.Element;
} {
  return {
    wrapper: ({ children }) => (
      <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
    ),
  };
}

const SAMPLE_USER = {
  id: 'u1',
  email: 'me@example.com',
  created_at: '2026-04-30T00:00:00Z',
  inbox_token: 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4',
  inbox_address: 'receipts+a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4@inbox.spendlens.local',
};

const writeText = vi.fn().mockResolvedValue(undefined);
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText },
  writable: true,
});

afterEach(() => {
  writeText.mockClear();
});

describe('InboxAddressCard', () => {
  it('renders nothing when no user is loaded', () => {
    const { container } = render(<InboxAddressCard />, {
      wrapper: withUser({
        token: null,
        user: undefined,
        isLoading: false,
        logout: vi.fn(),
      }).wrapper,
    });
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the address and the section copy', () => {
    render(<InboxAddressCard />, {
      wrapper: withUser({
        token: 'tok',
        user: SAMPLE_USER,
        isLoading: false,
        logout: vi.fn(),
      }).wrapper,
    });

    expect(screen.getByText(SAMPLE_USER.inbox_address)).toBeInTheDocument();
    expect(screen.getByText(/Forward any receipt email here/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
  });

  it('copies the address to the clipboard on click', async () => {
    render(<InboxAddressCard />, {
      wrapper: withUser({
        token: 'tok',
        user: SAMPLE_USER,
        isLoading: false,
        logout: vi.fn(),
      }).wrapper,
    });

    // ``fireEvent`` rather than ``userEvent`` because the latter
    // installs its own clipboard stub on setup, which collides with
    // the ``Object.defineProperty`` patch above.
    fireEvent.click(screen.getByRole('button', { name: /copy/i }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(SAMPLE_USER.inbox_address);
    });
    // Button label flips to "Copied" — the user needs the feedback.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /copied/i })).toBeInTheDocument();
    });
  });
});
