import b from './Banner.module.scss';

/** Names each failed request (`[what, error]`); renders nothing when none failed. */
export default function FailureBanner({
  failures,
  className,
}: {
  failures: readonly (readonly [string, Error | null])[];
  className?: string | undefined;
}) {
  const lines = [...new Set(failures.flatMap(([what, e]) => (e === null ? [] : [`${what}: ${e.message}`])))];
  if (lines.length === 0) return null;
  return (
    <div className={[b.banner, b.error, className].filter(Boolean).join(' ')} role="alert">
      <strong>API error</strong>
      <ul>
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}
