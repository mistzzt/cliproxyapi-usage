// Display helpers for model ids: `claude-sonnet-4-6` -> `sonnet-4.6`, grouped by family.

export type ModelFamily = 'Claude' | 'GPT' | 'Other';

export function modelFamily(model: string): ModelFamily {
  if (model.startsWith('claude')) return 'Claude';
  if (/^(gpt|codex|o\d)/.test(model)) return 'GPT';
  return 'Other';
}

/** Drops the `claude-` prefix and a trailing `-YYYYMMDD` date; `4-6` becomes `4.6`. */
export function shortModel(model: string): string {
  return model
    .replace(/^claude-/, '')
    .replace(/-\d{8}$/, '')
    .replace(/(\d+)-(\d+)/g, '$1.$2');
}
