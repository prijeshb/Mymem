import { Alert, Button } from '@heroui/react';

interface Props {
  message: string;
  onDismiss?: () => void;
}

export function ErrorBanner({ message, onDismiss }: Props) {
  return (
    <Alert status="danger" role="alert" className="flex items-start gap-3">
      <Alert.Indicator>⚠</Alert.Indicator>
      <Alert.Content>
        <Alert.Description>{message}</Alert.Description>
      </Alert.Content>
      {onDismiss && (
        <Button
          variant="ghost"
          isIconOnly
          size="sm"
          onPress={onDismiss}
          aria-label="Dismiss error"
          className="ml-auto shrink-0"
        >
          ✕
        </Button>
      )}
    </Alert>
  );
}
