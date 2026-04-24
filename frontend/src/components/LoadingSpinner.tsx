import { Spinner } from '@heroui/react';

interface Props {
  size?: 'sm' | 'md' | 'lg';
  label?: string;
}

export function LoadingSpinner({ size = 'md', label = 'Loading…' }: Props) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-6 text-gray-500 dark:text-gray-400" role="status" aria-live="polite">
      <Spinner size={size} />
      <span className="text-sm">{label}</span>
    </div>
  );
}
