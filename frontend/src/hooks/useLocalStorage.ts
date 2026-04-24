import { useState } from 'react';

export function useLocalStorage<T>(key: string, defaultValue: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return defaultValue;
      return JSON.parse(raw) as T;
    } catch {
      return defaultValue;
    }
  });

  const setter = (v: T) => {
    localStorage.setItem(key, JSON.stringify(v));
    setValue(v);
  };

  return [value, setter];
}
