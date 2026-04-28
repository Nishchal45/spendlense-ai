import { describe, expect, it } from 'vitest';

import { formatDate, formatMoney } from './format';

describe('formatMoney', () => {
  it('renders USD with dollar sign and two decimals', () => {
    // ``Intl`` output varies by locale runtime, so we anchor on the
    // presence of the currency marker rather than an exact string.
    const result = formatMoney('4.75', 'USD');
    expect(result).toContain('4.75');
    expect(result).toMatch(/\$|USD/);
  });

  it('respects thousand separators', () => {
    const result = formatMoney('1234.56', 'USD');
    expect(result).toMatch(/1,234\.56|1.234,56/);
  });

  it('handles non-USD currencies', () => {
    const result = formatMoney('9.99', 'EUR');
    expect(result).toContain('9.99');
    expect(result).toMatch(/€|EUR/);
  });
});

describe('formatDate', () => {
  it('renders an ISO date as a localised short form', () => {
    const result = formatDate('2026-04-25');
    // Don't pin on locale-specific glyphs; check the year + day are
    // both present so we know the parser didn't shift the date.
    expect(result).toContain('2026');
    expect(result).toContain('25');
  });

  it('does not shift the day around midnight UTC', () => {
    // ``new Date('2026-01-01')`` parsed as UTC becomes 2025-12-31 in
    // negative time zones. The formatter splits the string manually
    // to dodge that — verify by checking the year stays put.
    expect(formatDate('2026-01-01')).toContain('2026');
  });

  it('returns the input verbatim on a malformed date', () => {
    expect(formatDate('garbage')).toBe('garbage');
  });
});
