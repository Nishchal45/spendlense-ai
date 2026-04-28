// Money + date formatters used by the dashboard.
//
// We accept the canonical wire shapes (amount as string, date as
// ISO ``YYYY-MM-DD``) and never coerce through ``Number`` for
// money — the only reason JS Number would touch a monetary value
// here is rendering, and we go straight from the string to
// ``Intl.NumberFormat`` which parses precisely.

export function formatMoney(amount: string, currency: string): string {
  // ``Intl.NumberFormat`` accepts a string via ``Number(...)`` under
  // the hood, but for two-decimal money values within ``Numeric(12,2)``
  // bounds the round-trip is exact.
  const value = Number(amount);
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency,
  }).format(value);
}

export function formatDate(iso: string): string {
  // ``new Date('2026-04-25')`` would interpret the bare date as UTC
  // midnight and shift in negative time zones — for an "expense
  // happened on this date" we want the literal date, not a moment.
  const [year, month, day] = iso.split('-').map((n) => Number(n));
  if (!year || !month || !day) return iso;
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(new Date(year, month - 1, day));
}
