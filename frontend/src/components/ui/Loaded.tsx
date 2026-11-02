import type { ReactNode } from 'react';
import LoadingSpinner from './LoadingSpinner';
import s from './Loaded.module.scss';

/** Renders a query's data, a failure note when its request failed (details are in the page's error banner), or a spinner. */
export default function Loaded<T>({
  query,
  children,
}: {
  query: { data: T | undefined; isError: boolean };
  children: (data: T) => ReactNode;
}) {
  if (query.isError) return <div className={s.failed}>Failed to load.</div>;
  if (query.data !== undefined) return children(query.data);
  return <LoadingSpinner />;
}
