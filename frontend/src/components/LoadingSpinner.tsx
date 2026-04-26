import { Spinner } from '@heroui/react';
import { useEffect, useState } from 'react';

const LOADING_WORDS = [
  'Considering', 'Deliberating', 'Crafting',   'Synthesizing',
  'Reasoning',   'Brewing',      'Deciphering', 'Composing',
  'Analyzing',   'Weaving',      'Pondering',   'Distilling',
  'Connecting',  'Cerebrating',  'Imagining',   'Retrieving',
];

interface Props {
  size?: 'sm' | 'md' | 'lg';
  label?: string;
  className?: string;
}

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

  const text = label ?? `${LOADING_WORDS[index]}…`;

  return (
    <div
      className={`flex flex-col items-center justify-center gap-3 py-6 ${className}`}
      role="status"
      aria-live="polite"
      aria-label={text}
    >
      <Spinner size={size} color="primary" />
      <span className={`loading-word text-sm font-medium text-[#2563EB] dark:text-[#6ea8fe] ${visible ? 'loading-word--in' : ''}`}>
        {text}
      </span>
    </div>
  );
}
