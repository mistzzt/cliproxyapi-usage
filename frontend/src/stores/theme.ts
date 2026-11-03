import { create } from 'zustand';

import type { Theme } from '@/utils/usage/palette';

interface ThemeState {
  theme: Theme;
  toggle: () => void;
}

// Must match the inline script in index.html, which picks the theme before first paint.
const STORAGE_KEY = 'cliproxy-theme';

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
  toggle(): void {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try {
      sessionStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage can be unavailable (quota, sandboxing); the theme still applies for this page.
    }
    set({ theme: next });
  },
}));
