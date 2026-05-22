'use client';

import * as TabsPrimitive from '@radix-ui/react-tabs';
import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type ElementRef,
} from 'react';

import { cn } from '@/lib/utils';

export const Tabs = TabsPrimitive.Root;

export const TabsList = forwardRef<
  ElementRef<typeof TabsPrimitive.List>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(function TabsList({ className, ...rest }, ref) {
  return (
    <TabsPrimitive.List
      ref={ref}
      className={cn(
        'inline-flex h-10 items-center justify-start gap-1 rounded-md bg-neutral-100 p-1',
        'dark:bg-neutral-900',
        className,
      )}
      {...rest}
    />
  );
});

export const TabsTrigger = forwardRef<
  ElementRef<typeof TabsPrimitive.Trigger>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(function TabsTrigger({ className, ...rest }, ref) {
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium ring-offset-white',
        'transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-neutral-900',
        'disabled:pointer-events-none disabled:opacity-50',
        'data-[state=active]:bg-white data-[state=active]:text-neutral-900 data-[state=active]:shadow',
        'dark:ring-offset-neutral-950 dark:focus-visible:ring-neutral-100',
        'dark:data-[state=active]:bg-neutral-950 dark:data-[state=active]:text-neutral-100',
        className,
      )}
      {...rest}
    />
  );
});

export const TabsContent = forwardRef<
  ElementRef<typeof TabsPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(function TabsContent({ className, ...rest }, ref) {
  return (
    <TabsPrimitive.Content
      ref={ref}
      className={cn(
        'mt-4 ring-offset-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-neutral-900',
        'dark:ring-offset-neutral-950 dark:focus-visible:ring-neutral-100',
        className,
      )}
      {...rest}
    />
  );
});
