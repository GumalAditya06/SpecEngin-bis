export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="mx-auto mt-8 max-w-[820px] rounded-[var(--r-card)] border border-line bg-surface p-12 text-center"
    >
      <p className="text-lg font-medium text-ink">
        Something went wrong loading this view.
      </p>
      <p className="mx-auto mt-2 max-w-[48ch] text-sm text-muted">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="button button-primary mt-6"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function Skeletons({ rows = 3 }: { rows?: number }) {
  return (
    <div className="mx-auto mt-8 max-w-[820px] space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="skeleton h-20 rounded-[var(--r-card)] border border-line bg-surface"
          aria-hidden
        />
      ))}
    </div>
  );
}
