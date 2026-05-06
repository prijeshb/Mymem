import { useEffect, useState } from 'react';

const INGEST_WORDS = [
  'Chewing on this…',
  'Talking to the void…',
  'Bribing the neurons…',
  'Filing under "important"…',
  'Arguing with tokens…',
  'Connecting the dots (and some dashes)…',
  'Extracting the good stuff…',
  'Pretending to think…',
  'Turning words into wisdom…',
  'Almost definitely working…',
  'Making stuff up responsibly…',
  'Consulting the wiki oracle…',
];

interface Props {
  className?: string;
}

export function ClaudeLoader({ className = '' }: Props) {
  const [index, setIndex] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const id = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setIndex(i => (i + 1) % INGEST_WORDS.length);
        setVisible(true);
      }, 280);
    }, 2200);
    return () => clearInterval(id);
  }, []);

  return (
    <div
      className={`flex flex-col items-center gap-4 py-6 ${className}`}
      role="status"
      aria-live="polite"
      aria-label={INGEST_WORDS[index]}
    >
      <div className="flex items-center gap-1.5">
        {[0, 1, 2].map(i => (
          <span
            key={i}
            className="w-2 h-2 rounded-full bg-indigo-400 animate-bounce"
            style={{ animationDelay: `${i * 160}ms`, animationDuration: '900ms' }}
          />
        ))}
      </div>
      <span
        className={`text-sm font-medium text-indigo-400 transition-opacity duration-200 ${
          visible ? 'opacity-100' : 'opacity-0'
        }`}
      >
        {INGEST_WORDS[index]}
      </span>
    </div>
  );
}
