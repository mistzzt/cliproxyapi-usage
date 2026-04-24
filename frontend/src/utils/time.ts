/**
 * Formats an ISO-8601 timestamp relative to now.
 * Examples: "in 2h 15m", "in 3d", "5 min ago"
 */
export function formatRelative(iso: string): string {
  const target = new Date(iso).getTime();
  const now = Date.now();
  const diffMs = target - now;
  const absDiffMs = Math.abs(diffMs);
  const isFuture = diffMs > 0;

  const seconds = Math.floor(absDiffMs / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  let label: string;
  if (days >= 1) {
    const remHours = hours - days * 24;
    label = remHours > 0 ? `${days}d ${remHours}h` : `${days}d`;
  } else if (hours >= 1) {
    const remMinutes = minutes - hours * 60;
    label = remMinutes > 0 ? `${hours}h ${remMinutes}m` : `${hours}h`;
  } else if (minutes >= 1) {
    label = `${minutes} min`;
  } else {
    label = 'just now';
    return label;
  }

  return isFuture ? `in ${label}` : `${label} ago`;
}
