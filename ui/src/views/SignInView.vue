<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiButton, UiCallout, UiCard, UiFormField, UiInput } from '@/components/ui'
import { ApiError, formatApiError } from '@/lib/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const action = ref<'login' | 'register' | 'recover' | 'resend'>('login')
const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const recovery = computed(() => auth.state.kind === 'recovery')
const title = computed(() => recovery.value ? 'Reset password' : ({
  login: 'Sign in', register: 'Create account', recover: 'Reset password', resend: 'Verify email',
})[action.value])
const needsPassword = computed(() => recovery.value || ['login', 'register'].includes(action.value))

function choose(value: typeof action.value): void {
  action.value = value
  password.value = error.value = notice.value = ''
}

async function submit(): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  const selected = recovery.value ? 'password' : action.value
  try {
    await auth.submit(selected, {
      ...(selected === 'password' ? {} : { email: email.value.trim() }),
      ...(needsPassword.value ? { password: password.value } : {}),
    })
    if (selected === 'login') {
      // A fresh document drops all prior user's project caches and streams.
      window.location.assign('/')
    } else if (selected === 'password') {
      action.value = 'login'
      notice.value = 'Password updated. Sign in with your new password.'
    } else {
      notice.value = selected === 'recover'
        ? 'Check your email for a password reset link. Open it and confirm on this StackOS server.'
        : 'Check your email for a verification link. Open it and confirm on this StackOS server.'
    }
  } catch (err) {
    const code = err instanceof ApiError && typeof err.body === 'object' && err.body !== null
      ? (err.body as { detail?: { code?: string } }).detail?.code : undefined
    if (code === 'session_expired_or_invalid' || code === 'session_invalid') auth.expire()
    error.value = ({
      sign_in_failed_or_email_unverified: 'Sign-in failed. Check your email and password, or verify your email first.',
      verification_expired_or_invalid: 'This link has expired or was already used. Request another email.',
      retry_later: 'Too many attempts. Wait before trying again.',
      auth_unavailable: 'Sign-in is temporarily unavailable. Try again shortly.',
      session_expired_or_invalid: 'Your session has expired. Sign in again or request another reset email.',
    } as Record<string, string>)[code ?? ''] ?? formatApiError(err, 'Authentication failed. Try again.')
  } finally {
    password.value = ''
    busy.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-bg-app px-4 py-8 text-fg-default">
    <UiCard class="w-full max-w-md">
      <p class="mb-2 text-sm font-semibold text-fg-muted">
        StackOS
      </p>
      <h1 class="text-xl font-semibold text-fg-strong">
        {{ title }}
      </h1>
      <p class="mt-2 break-all text-xs text-fg-muted">
        {{ auth.environmentId ?? 'Private access' }}
      </p>
      <UiCallout
        v-if="error"
        tone="danger"
        class="mt-4"
        role="alert"
      >
        {{ error }}
      </UiCallout>
      <UiCallout
        v-if="notice"
        tone="success"
        class="mt-4"
        role="status"
      >
        {{ notice }}
      </UiCallout>
      <form
        class="mt-5 flex flex-col gap-4"
        @submit.prevent="submit"
      >
        <UiFormField
          v-if="!recovery"
          label="Email"
          required
        >
          <UiInput
            v-model="email"
            type="email"
            autocomplete="email"
            :disabled="busy"
            required
          />
        </UiFormField>
        <UiFormField
          v-if="needsPassword"
          label="Password"
          required
          :help="action === 'register' || recovery ? 'Use 8–1024 characters.' : undefined"
        >
          <UiInput
            v-model="password"
            type="password"
            :autocomplete="action === 'login' && !recovery ? 'current-password' : 'new-password'"
            minlength="8"
            maxlength="1024"
            :disabled="busy"
            required
          />
        </UiFormField>
        <UiButton
          type="submit"
          variant="primary"
          :loading="busy"
        >
          {{ recovery ? 'Save password' : title }}
        </UiButton>
      </form>
      <div
        v-if="!recovery"
        class="mt-4 flex flex-wrap gap-2"
      >
        <UiButton
          v-if="action !== 'login'"
          variant="ghost"
          :disabled="busy"
          @click="choose('login')"
        >
          Sign in
        </UiButton>
        <UiButton
          v-if="action !== 'register'"
          variant="ghost"
          :disabled="busy"
          @click="choose('register')"
        >
          Create account
        </UiButton>
        <UiButton
          v-if="action !== 'recover'"
          variant="ghost"
          :disabled="busy"
          @click="choose('recover')"
        >
          Forgot password?
        </UiButton>
        <UiButton
          v-if="action !== 'resend'"
          variant="ghost"
          :disabled="busy"
          @click="choose('resend')"
        >
          Resend verification email
        </UiButton>
      </div>
    </UiCard>
  </div>
</template>
