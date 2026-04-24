// Resolve the theme-driven Chart.js palette from CSS custom properties on
// <html>. Must be called inside a memo keyed on the current theme so it
// recomputes when the user toggles. Returns already-trimmed strings safe to
// pass straight to Chart.js options.
export interface ChartThemeColors {
  grid: string;
  tick: string;
  surface: string;
  text: string;
  // --color-text-muted doubles as tooltip border so the tooltip reads clearly
  // against the page background (--color-border is too faint for that role).
  tooltipBorder: string;
}

export function readThemeColors(): ChartThemeColors {
  const cs = getComputedStyle(document.documentElement);
  const muted = cs.getPropertyValue('--color-text-muted').trim();
  return {
    grid: cs.getPropertyValue('--color-border').trim(),
    tick: muted,
    surface: cs.getPropertyValue('--color-surface').trim(),
    text: cs.getPropertyValue('--color-text').trim(),
    tooltipBorder: muted,
  };
}

// Fully theme-wired Chart.js tooltip config. Spread into
// `options.plugins.tooltip` so the three chart files stay in lock-step.
export interface ChartTooltipConfig {
  enabled: true;
  backgroundColor: string;
  titleColor: string;
  bodyColor: string;
  borderColor: string;
  borderWidth: 1;
  padding: 10;
  cornerRadius: 6;
}

export function buildTooltipConfig(colors: ChartThemeColors): ChartTooltipConfig {
  return {
    enabled: true,
    backgroundColor: colors.surface,
    titleColor: colors.text,
    bodyColor: colors.text,
    borderColor: colors.tooltipBorder,
    borderWidth: 1,
    padding: 10,
    cornerRadius: 6,
  };
}
