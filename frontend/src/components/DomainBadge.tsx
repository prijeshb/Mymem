import { Chip } from '@heroui/react';

const DOMAIN_CLASSES: Record<string, string> = {
  tech:      'bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300',
  spiritual: 'bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300',
  finance:   'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
  health:    'bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300',
  reminder:  'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300',
  research:  'bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300',
  personal:  'bg-yellow-100 text-yellow-700 dark:bg-yellow-950 dark:text-yellow-300',
  creative:  'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300',
  business:  'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300',
  misc:      'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300',
};

interface Props {
  domain: string;
  className?: string;
}

export function DomainBadge({ domain, className = '' }: Props) {
  const cls = DOMAIN_CLASSES[domain] ?? DOMAIN_CLASSES['misc'];
  return (
    <Chip size="sm" className={`${cls} ${className}`}>
      {domain}
    </Chip>
  );
}
