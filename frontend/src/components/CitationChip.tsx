import { Link } from 'react-router-dom';
import { Chip } from '@heroui/react';
import { titleToSlug } from '../lib/api';

interface Props {
  title: string;
}

export function CitationChip({ title }: Props) {
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
