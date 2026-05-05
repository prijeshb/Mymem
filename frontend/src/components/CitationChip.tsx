import { Link } from 'react-router-dom';
import { Chip } from '@heroui/react';
import { titleToSlug } from '../lib/api';

interface Props {
  title: string;
}

export function CitationChip({ title }: Props) {
  const isPdf = title.startsWith('[PDF:');

  if (isPdf) {
    return (
      <Chip
        size="sm"
        className="font-mono bg-amber-50 border border-amber-300 text-amber-800
                   dark:bg-amber-950 dark:border-amber-700 dark:text-amber-300"
      >
        {title}
      </Chip>
    );
  }

  return (
    <Link to={`/wiki/${titleToSlug(title)}`} tabIndex={-1}>
      <Chip
        size="sm"
        className="font-mono bg-blue-50 border border-blue-300 text-blue-700
                   hover:border-blue-500 hover:text-blue-900 transition-colors cursor-pointer"
      >
        [[{title}]]
      </Chip>
    </Link>
  );
}
