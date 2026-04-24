import { create } from 'zustand';

export type ResolvedTheme = 'light' | 'dark';

interface ThemeState {
  theme: ResolvedTheme;
  setTheme: (t: ResolvedTheme) => void;
  toggle: () => void;
}

const STORAGE_KEY = 'cliproxy-theme';

function readInitialTheme(): ResolvedTheme {
  try {
    if (typeof window === 'undefined' || typeof document === 'undefined') {
      return 'light';
    }
    const stored = window.sessionStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') {
      return stored;
    }
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

function applyTheme(theme: ResolvedTheme): void {
  try {
    if (typeof document !== 'undefined') {
      document.documentElement.dataset.theme = theme;
    }
  } catch {
    // Ignore: DOM not available (SSR, sandboxed iframe).
  }
  try {
    if (typeof window !== 'undefined') {
      window.sessionStorage.setItem(STORAGE_KEY, theme);
    }
  } catch {
    // Ignore: sessionStorage quota or sandbox restriction.
  }
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: readInitialTheme(),
  setTheme(t: ResolvedTheme): void {
    applyTheme(t);
    set({ theme: t });
  },
  toggle(): void {
    const next: ResolvedTheme = get().theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    set({ theme: next });
  },
}));
