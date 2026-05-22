'use client';

import { useState, type ReactNode } from 'react';

import { Button, type ButtonVariant } from '@/components/ui/Button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/Dialog';
import { Spinner } from '@/components/ui/Spinner';
import { ErrorBlock } from '@/components/admin/ErrorBlock';

/**
 * Confirmation modal used for destructive actions: archive, evict,
 * deactivate.
 *
 * The ``onConfirm`` async callback is awaited inside the dialog so we
 * can render a spinner during the round-trip and surface any backend
 * error in-place — much better UX than the page-level error banner
 * for a single-action flow.  The dialog auto-closes on success.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  confirmVariant = 'destructive',
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmVariant?: ButtonVariant;
  onConfirm: () => Promise<void> | void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function handleClick() {
    setError(null);
    setPending(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  // Reset state when the consumer closes the dialog.
  function handleOpenChange(next: boolean) {
    if (!next) {
      setError(null);
      setPending(false);
    }
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? (
            <DialogDescription>{description}</DialogDescription>
          ) : null}
        </DialogHeader>
        {error ? <ErrorBlock error={error} /> : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={pending}>
              {cancelLabel}
            </Button>
          </DialogClose>
          <Button
            variant={confirmVariant}
            onClick={handleClick}
            disabled={pending}
          >
            {pending ? <Spinner className="text-white" /> : null}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
