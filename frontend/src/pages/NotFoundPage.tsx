import { useNavigate } from 'react-router-dom';
import { Button } from '@heroui/react';

export function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <p className="text-6xl font-bold text-gray-700 mb-4">404</p>
      <h1 className="text-xl font-semibold text-gray-300 mb-2">Page not found</h1>
      <p className="text-sm text-gray-500 mb-6">
        This page doesn't exist in your wiki.
      </p>
      <Button variant="primary" size="sm" onPress={() => navigate('/')}>
        Back to Dashboard
      </Button>
    </div>
  );
}
