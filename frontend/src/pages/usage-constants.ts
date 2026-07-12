/**
 * The explorer draws at most seven named series. This is satisfied two ways:
 * an explicit model selection of up to seven models, or automatic
 * decomposition into the top six models plus a derived `Other` series (also
 * seven). There is no separate cap for the two paths.
 */
export const MAX_EXPLORER_SERIES = 7;

/**
 * Explicit model selection is capped at the named-series limit (used as the
 * `maxSelection` for the Models filter in the sidebar).
 */
export const MODEL_SELECTION_MAX = MAX_EXPLORER_SERIES;
