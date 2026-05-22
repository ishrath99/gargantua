'use client';

/**
 * Username + password login.
 *
 * Form state via ``react-hook-form`` + ``zod`` — overkill for two
 * fields, but the pattern is what every form in PR 16 will use,
 * so we lock the shape in here.
 *
 * Wrapped in :component:`GuestOnly` so an already-logged-in caller
 * who clicks the bookmarked ``/login/`` URL goes straight to ``/``.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';

import { ApiError } from '@/lib/api/client';
import { useAuth } from '@/lib/auth/context';
import { GuestOnly } from '@/components/RouteGuard';

const schema = z.object({
  username: z.string().min(1, 'Username is required'),
  password: z.string().min(1, 'Password is required'),
});

type FormValues = z.infer<typeof schema>;

function LoginForm() {
  const router = useRouter();
  const { login } = useAuth();
  const [submitError, setSubmitError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { username: '', password: '' },
  });

  const onSubmit = async (values: FormValues) => {
    setSubmitError(null);
    try {
      await login(values);
      router.replace('/');
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setSubmitError('Invalid username or password.');
        } else if (err.status === 0 || err.status >= 500) {
          setSubmitError('The server is not reachable. Please try again.');
        } else {
          setSubmitError(`Login failed (${err.status}).`);
        }
      } else {
        setSubmitError('An unexpected error occurred.');
      }
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-neutral-50 p-4 dark:bg-neutral-950">
      <form
        onSubmit={handleSubmit(onSubmit)}
        className="w-full max-w-sm space-y-4 rounded-lg border border-neutral-200 bg-white p-6 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
        aria-label="Login form"
      >
        <header className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">Sign in</h1>
          <p className="text-sm text-neutral-500">gargantua admin console</p>
        </header>

        <div className="space-y-1">
          <label htmlFor="username" className="block text-sm font-medium">
            Username
          </label>
          <input
            id="username"
            autoComplete="username"
            spellCheck={false}
            className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-neutral-500 focus:outline-none dark:border-neutral-700 dark:bg-neutral-950"
            aria-invalid={errors.username ? 'true' : 'false'}
            {...register('username')}
          />
          {errors.username ? (
            <p role="alert" className="text-xs text-red-600">
              {errors.username.message}
            </p>
          ) : null}
        </div>

        <div className="space-y-1">
          <label htmlFor="password" className="block text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-neutral-500 focus:outline-none dark:border-neutral-700 dark:bg-neutral-950"
            aria-invalid={errors.password ? 'true' : 'false'}
            {...register('password')}
          />
          {errors.password ? (
            <p role="alert" className="text-xs text-red-600">
              {errors.password.message}
            </p>
          ) : null}
        </div>

        {submitError ? (
          <p
            role="alert"
            className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300"
          >
            {submitError}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-60 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-200"
        >
          {isSubmitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  );
}

export default function LoginPage() {
  return (
    <GuestOnly>
      <LoginForm />
    </GuestOnly>
  );
}
