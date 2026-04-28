import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { RECEIPT_STATUSES } from '@/api/receipts';
import { StatusBadge } from './StatusBadge';

describe('StatusBadge', () => {
  it('renders the human label for each status', () => {
    const labels: Record<string, string> = {
      uploaded: 'Queued',
      processing: 'Processing',
      parsed: 'Read',
      categorised: 'Categorised',
      failed: 'Failed',
    };

    for (const status of RECEIPT_STATUSES) {
      const { unmount } = render(<StatusBadge status={status} />);
      expect(screen.getByText(labels[status]!)).toBeInTheDocument();
      unmount();
    }
  });

  it('decorates active states with a pulsing dot', () => {
    const { container } = render(<StatusBadge status="processing" />);
    // The dot is ``aria-hidden`` (decoration only) — assert via class
    // rather than ``getByRole`` since we deliberately keep it out of
    // the accessibility tree.
    expect(container.querySelector('.animate-pulse')).not.toBeNull();
  });

  it('does not pulse on terminal states', () => {
    const { container } = render(<StatusBadge status="categorised" />);
    expect(container.querySelector('.animate-pulse')).toBeNull();
  });
});
