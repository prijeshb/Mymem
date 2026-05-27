import { useEffect, useState } from 'react';

const LOADING_WORDS = [
  'Doing computer things…',
  'Herding electrons…',
  'Asking nicely…',
  'Untangling spaghetti…',
  'Summoning data spirits…',
  'Counting to infinity (almost)…',
  'Blaming the server…',
  'Definitely not sleeping…',
  'Buffering existentially…',
  'Warming up the hamsters…',
  'Negotiating with the database…',
  'Loading… loading… loaded?…',
];

interface Props {
  size?: 'sm' | 'md' | 'lg';
  label?: string;
  className?: string;
}

const RING: Record<string, string> = {
  sm: 'w-5 h-5 border-2',
  md: 'w-7 h-7 border-2',
  lg: 'w-10 h-10 border-[3px]',
};

export function LoadingSpinner({ size = 'md', label, className = '' }: Props) {
  const [index, setIndex] = useState(() => Math.floor(Math.random() * LOADING_WORDS.length));
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    if (label) return;
    const id = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setIndex(i => (i + 1) % LOADING_WORDS.length);
        setVisible(true);
      }, 280);
    }, 2000);
    return () => clearInterval(id);
  }, [label]);

  const text = label ?? LOADING_WORDS[index];

  return (
    <div
      className={`flex flex-col items-center justify-center gap-3 py-6 ${className}`}
      role="status"
      aria-live="polite"
      aria-label={text}
    >
      <div
        className={`${RING[size]} rounded-full border-gray-600 border-t-sky-400 animate-spin`}
      />
      <span
        className={`text-sm font-medium text-sky-400 transition-opacity duration-200 ${
          visible ? 'opacity-100' : 'opacity-0'
        }`}
      >
        {text}
      </span>
    </div>
  );
}
