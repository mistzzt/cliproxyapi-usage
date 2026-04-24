/** Maximum number of chart lines the selector allows selecting at once. */
export const CHART_MAX_LINES = 9;

/**
 * `top_n` value passed to the timeseries API when in "all models" mode.
 * One slot is reserved for the `__all__` aggregate series.
 */
export const CHART_TOP_N = CHART_MAX_LINES - 1; // 8
