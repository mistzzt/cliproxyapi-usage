// Chart.js colors read from the theme's CSS custom properties on <html>.
// Call it in a memo keyed on the theme, so a toggle reads them again.
export interface ChartThemeColors {
  grid: string;
  tick: string;
  surface: string;
  text: string;
  // The muted text color: --color-border is too faint to outline a tooltip.
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

/** Theme-colored Chart.js tooltip options; spread into `options.plugins.tooltip`. */
export function buildTooltipConfig(colors: ChartThemeColors) {
  return {
    enabled: true as const,
    backgroundColor: colors.surface,
    titleColor: colors.text,
    bodyColor: colors.text,
    borderColor: colors.tooltipBorder,
    borderWidth: 1,
    padding: 10,
    cornerRadius: 6,
  };
}
