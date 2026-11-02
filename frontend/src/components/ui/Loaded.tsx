import type { ReactNode } from 'react';
import LoadingSpinner from './LoadingSpinner';
import s from './Loaded.module.scss';

/** Renders loaded data, a failure note when its request failed (details are in the page's error banner), or a spinner. */
export default function Loaded<T>({
  api,
  children,
}: {
  api: { data: T | null; error: string | null };
  children: (data: T) => ReactNode;
}) {
  if (api.data !== null) return children(api.data);
  if (api.error !== null) return <div className={s.failed}>Failed to load.</div>;
  return <LoadingSpinner />;
}
