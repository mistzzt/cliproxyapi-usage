// Validated categorical palette: seven series slots per theme plus an "Other" gray (pure).

export type Theme = 'light' | 'dark';

export const SERIES_COLORS: Record<Theme, readonly string[]> = {
  light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6'],
  dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9'],
};

export const OTHER_COLOR: Record<Theme, string> = { light: '#b5b4ad', dark: '#5f5e59' };

/** Single-series accent (sparklines, unsplit charts); equals slot 0. */
export const ACCENT_COLOR: Record<Theme, string> = { light: '#2a78d6', dark: '#3987e5' };

// Light fills where white text fails contrast.
const DARK_INK_FILLS = new Set(['#eda100', '#c98500', '#b5b4ad']);

/** Slot color, or the Other gray for a null or out-of-range slot. */
export function slotColor(slot: number | null, theme: Theme): string {
  return (slot !== null ? SERIES_COLORS[theme][slot] : undefined) ?? OTHER_COLOR[theme];
}

/** Text color for a label drawn on `fill`. */
export function inkOn(fill: string): string {
  return DARK_INK_FILLS.has(fill) ? '#0b0b0b' : '#ffffff';
}
