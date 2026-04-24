/**
 * Local-date helpers — always use local clock, never UTC.
 * toISOString() returns UTC which shifts the date for users in UTC+/- zones.
 */

/** YYYY-MM-DD for today using the local clock. */
export function localIsoToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/**
 * Format a YYYY-MM-DD string as a human-readable date (local timezone).
 * Uses T12:00:00 to prevent midnight UTC-shift when parsing.
 */
export function formatDate(
  isoDate: string,
  options: Intl.DateTimeFormatOptions = { weekday: 'long', month: 'long', day: 'numeric' },
): string {
  return new Date(isoDate + 'T12:00:00').toLocaleDateString('en-US', options);
}

/** Format a full ISO timestamp as HH:MM (24-hour, local clock). */
export function formatTime(isoTimestamp: string): string {
  return new Date(isoTimestamp).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/** Full human-readable label for today, e.g. "Monday, April 21, 2026". */
export function formatToday(): string {
  return new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}
