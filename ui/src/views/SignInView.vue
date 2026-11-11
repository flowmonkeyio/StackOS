<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { UiButton, UiFormField, UiIcon, UiInput } from '@/components/ui'
import { ApiError } from '@/lib/client'
import { desktop, isDesktopShell } from '@/lib/desktop'
import { authDestination, authErrorCode, useAuthStore } from '@/stores/auth'

type Screen = 'login' | 'register' | 'recover' | 'check-email' | 'confirm' | 'verified' | 'password' | 'updated' | 'link-error'
type Purpose = 'signup' | 'recovery' | 'unknown'
const auth = useAuthStore()
const route = useRoute()
const initial = new URL(window.location.href)
const callback = initial.searchParams.get('auth') === 'callback'
const purpose = ref<Purpose>(initial.searchParams.get('purpose') === 'signup' ? 'signup' : initial.searchParams.get('purpose') === 'recovery' ? 'recovery' : 'unknown')
const outcome = ref(initial.searchParams.get('outcome') ?? '')
// The one-time hash stays only in this mounted view. A scanner-followed GET
// never exchanges it. Remove it before subsequent requests or navigation.
let tokenHash = initial.searchParams.get('token_hash') ?? ''
if (initial.searchParams.has('token_hash')) {
  initial.searchParams.delete('token_hash')
  window.history.replaceState(window.history.state, '', `${initial.pathname}${initial.search}${initial.hash}`)
}
const screen = ref<Screen>(auth.state.kind === 'recovery' ? 'password' : initial.searchParams.get('flow') === 'register' ? 'register' : initial.searchParams.get('flow') === 'recover' ? 'recover' : 'login')
const email = ref('')
const password = ref('')
const reveal = ref(false)
const busy = ref(false)
const error = ref('')
const feedback = ref('')
const heading = ref<HTMLElement | null>(null)
const desktopReturn = ref(false)
const returnKey = 'stackos-account-return'
// Presentation intent contains no credentials or session. It carries across
// email tabs in the default browser, scoped by that browser's exact origin.
try {
  const flow = initial.searchParams.get('flow')
  if (initial.searchParams.get('client') === 'desktop' && ['register', 'recover'].includes(flow ?? '')) {
    localStorage.setItem(returnKey, JSON.stringify({ purpose: flow === 'recover' ? 'recovery' : 'signup', until: Date.now() + 30 * 60_000 }))
  } else if (!callback && !flow) localStorage.removeItem(returnKey)
  const intent = JSON.parse(localStorage.getItem(returnKey) ?? '{}')
  desktopReturn.value = intent.until > Date.now() && (!callback || intent.purpose === purpose.value)
} catch { /* Normal browser continuation works when storage is disabled. */ }
if (callback) {
  if (['pending', 'retry'].includes(outcome.value) && tokenHash && purpose.value !== 'unknown') screen.value = 'confirm'
  else if (['success', 'sign_in_required'].includes(outcome.value) && purpose.value === 'signup') screen.value = 'verified'
  else if (outcome.value === 'success' && purpose.value === 'recovery' && auth.state.kind === 'recovery') screen.value = 'password'
  else screen.value = 'link-error'
}
const title = computed(() => ({
  login: 'Welcome back', register: 'Create your account', recover: 'Reset your password',
  'check-email': 'Check your email', confirm: purpose.value === 'recovery' ? 'Reset your password' : 'Verify your email',
  verified: 'Email verified', password: 'Choose a new password', updated: 'Password updated',
  'link-error': outcome.value === 'expired_or_used' ? 'This link has expired or was used' : 'This link is invalid',
})[screen.value])
const description = computed(() => ({
  login: 'Sign in to StackOS.', register: 'One account for your projects and work.',
  recover: 'Enter your email to receive a reset link.',
  'check-email': purpose.value === 'recovery' ? 'If an account exists, we sent a password reset link to' : 'We sent a verification link to',
  confirm: purpose.value === 'recovery' ? 'Continue to choose a new password.' : 'Confirm this email address to continue.',
  verified: desktopReturn.value ? 'Return to StackOS and sign in to continue.' : 'Your email is confirmed. Continue to StackOS.',
  password: '', updated: 'Sign in with your new password.',
  'link-error': purpose.value === 'recovery' ? 'Request a new password reset link to continue.' : purpose.value === 'signup' ? 'Send a new verification link to continue.' : 'Return to sign in or request a new email.',
})[screen.value])
const needsEmail = computed(() => ['login', 'register', 'recover'].includes(screen.value) || (screen.value === 'link-error' && purpose.value !== 'unknown'))
const needsPassword = computed(() => ['login', 'register', 'password'].includes(screen.value))
const primary = computed(() => ({ login: 'Sign in', register: 'Create account', recover: 'Send reset link', 'check-email': 'Resend email', confirm: purpose.value === 'recovery' ? 'Continue' : 'Verify email', verified: desktopReturn.value ? 'Open StackOS' : 'Continue to StackOS', password: 'Update password', updated: desktopReturn.value ? 'Open StackOS' : 'Sign in', 'link-error': purpose.value === 'unknown' ? 'Back to sign in' : 'Send a new link' })[screen.value])
const statusIcon = computed(() => screen.value === 'check-email' ? 'mail' : ['verified', 'updated'].includes(screen.value) ? 'check-circle' : screen.value === 'link-error' ? 'clock' : '')
const destination = computed(() => authDestination(route?.query?.returnTo ?? (route?.path !== '/sign-in' ? route?.fullPath : '/')))

function choose(value: Screen): void {
  if (busy.value) return
  // Standard external-browser signup/recovery; returning opens the normal
  // app sign-in, with no token transport between browser and app cookies.
  if (isDesktopShell() && (value === 'register' || value === 'recover')) {
    void desktop.openAccount(value).then(result => { if (result === null) error.value = 'Could not open your browser. Open StackOS in your browser to continue.' })
    return
  }
  screen.value = value
  password.value = error.value = feedback.value = ''
  reveal.value = false
  void nextTick(() => { heading.value?.focus() })
}
async function submit(): Promise<void> {
  if (busy.value) return
  if (screen.value === 'verified' || screen.value === 'updated') {
    if (desktopReturn.value) { clearReturnIntent(); window.location.assign('stackos://sign-in'); return }
    window.location.assign(screen.value === 'verified' ? destination.value : '/sign-in')
    return
  }
  if (screen.value === 'link-error' && purpose.value === 'unknown') { choose('login'); return }
  const previousScreen = screen.value
  busy.value = true
  error.value = feedback.value = ''
  const selected = screen.value === 'login' ? 'login' : screen.value === 'register' ? 'register' : screen.value === 'password' ? 'password' : screen.value === 'confirm' ? 'verify' : purpose.value === 'recovery' || screen.value === 'recover' ? 'recover' : 'resend'
  try {
    await auth.submit(selected, selected === 'verify' ? { token_hash: tokenHash, type: purpose.value } : {
      ...(selected === 'password' ? {} : { email: email.value.trim() }),
      ...(needsPassword.value ? { password: password.value } : {}),
    })
    if (selected === 'login') window.location.assign(destination.value)
    else if (selected === 'password') screen.value = 'updated'
    else if (selected === 'verify') { tokenHash = ''; screen.value = purpose.value === 'recovery' ? 'password' : 'verified' }
    else {
      purpose.value = selected === 'recover' ? 'recovery' : 'signup'
      if (screen.value === 'check-email') feedback.value = 'Email sent. Check your inbox and spam folder.'
      screen.value = 'check-email'
    }
  } catch (err) {
    const code = authErrorCode(err)
    const result = err instanceof ApiError ? (err.body as { detail?: { callback?: { outcome: string } } })?.detail?.callback : undefined
    if (selected === 'verify' && result) {
      outcome.value = result.outcome
      if (result.outcome !== 'retry') tokenHash = ''
      screen.value = result.outcome === 'sign_in_required' ? 'verified' : result.outcome === 'retry' ? 'confirm' : 'link-error'
    }
    if (['session_expired_or_invalid', 'session_invalid'].includes(code)) { auth.expire(); screen.value = 'recover' }
    error.value = ({
      sign_in_failed_or_email_unverified: 'Sign-in failed. Check your email and password, or verify your email first.',
      retry_later: 'Too many attempts. Wait a moment before trying again.',
      session_busy: 'Another account request is finishing. Try again shortly.',
      auth_unavailable: 'StackOS is temporarily unavailable. Try again shortly.',
      password_length: 'Use between 8 and 1024 characters.',
      session_expired_or_invalid: 'Your session has expired. Request a new password reset link.',
    } as Record<string, string>)[code] ?? (selected === 'verify' && result && result.outcome !== 'retry' ? '' : 'Could not complete this request. Try again.')
  } finally {
    password.value = ''; reveal.value = false; busy.value = false
    await nextTick()
    if (error.value) document.getElementById(needsPassword.value ? 'account-password' : needsEmail.value ? 'account-email' : 'account-error')?.focus()
    else if (screen.value !== previousScreen) heading.value?.focus()
  }
}
function clearReturnIntent(): void {
  try { localStorage.removeItem(returnKey) } catch { /* Storage can be disabled. */ }
}
function continueBrowser(): void {
  clearReturnIntent()
  desktopReturn.value = false
}
async function reconnect(): Promise<void> {
  busy.value = true
  try {
    if (auth.csrfToken) await auth.refreshSession(); else await auth.bootstrap()
    if (auth.state.kind === 'recovery') screen.value = 'password'
  } finally {
    busy.value = false
    await nextTick()
    heading.value?.focus()
  }
}
onMounted(() => { heading.value?.focus() })
</script>

<template>
  <main class="flex min-h-screen items-center justify-center bg-bg-app px-6 py-10 text-fg-default">
    <section
      class="w-full max-w-[400px]"
      aria-labelledby="account-heading"
    >
      <div class="mb-8 flex flex-col items-center gap-1.5">
        <img
          src="/images/stackos-icon.png"
          alt=""
          class="h-12 w-12 object-contain"
        >
        <span class="text-base font-semibold text-fg-strong">StackOS</span>
      </div>
      <template v-if="auth.state.kind === 'error' || auth.state.kind === 'renewing' || auth.state.kind === 'bootstrapping'">
        <h1
          id="account-heading"
          ref="heading"
          tabindex="-1"
          class="text-center text-xl font-semibold text-fg-strong"
        >
          {{ auth.state.kind === 'error' ? 'Unable to connect' : 'Reconnecting to StackOS' }}
        </h1>
        <p
          class="mt-3 text-center text-sm text-fg-muted"
          role="status"
        >
          {{ auth.state.kind === 'error' ? auth.state.message : 'Your session is being checked.' }}
        </p>
        <UiButton
          v-if="auth.state.kind === 'error'"
          class="mt-6 w-full"
          variant="primary"
          :loading="busy"
          @click="reconnect"
        >
          Try again
        </UiButton>
      </template>
      <template v-else>
        <UiIcon
          v-if="statusIcon"
          :name="statusIcon"
          :class="['mx-auto mb-4 h-8 w-8', screen === 'link-error' ? 'text-warning' : screen === 'check-email' ? 'text-info' : 'text-success']"
        />
        <h1
          id="account-heading"
          ref="heading"
          tabindex="-1"
          class="text-center text-xl font-semibold text-fg-strong"
        >
          {{ title }}
        </h1>
        <p
          v-if="description"
          class="mt-2 text-center text-sm text-fg-muted"
        >
          {{ description }}
        </p>
        <p
          v-if="screen === 'check-email'"
          class="mt-1 break-all text-center text-sm font-medium"
        >
          {{ email }}
        </p>
        <form
          class="mt-6 flex flex-col gap-4"
          :aria-busy="busy"
          @submit.prevent="submit"
        >
          <UiFormField
            v-if="needsEmail"
            label="Email"
            input-id="account-email"
            :error="error && !needsPassword ? error : undefined"
          >
            <UiInput
              v-model="email"
              type="email"
              autocomplete="username"
              autocapitalize="none"
              spellcheck="false"
              placeholder="you@example.com"
              size="lg"
              :disabled="busy"
              required
            />
          </UiFormField>
          <div
            v-if="needsPassword"
            class="relative"
          >
            <UiFormField
              :label="screen === 'password' ? 'New password' : 'Password'"
              input-id="account-password"
              :help="screen !== 'login' ? 'Use 8–1024 characters. A long, unique password is best.' : undefined"
              :error="error || undefined"
            >
              <template
                v-if="screen === 'login'"
                #label-actions
              >
                <UiButton
                  variant="link"
                  size="sm"
                  :disabled="busy"
                  @click="choose('recover')"
                >
                  Forgot password?
                </UiButton>
              </template>
              <UiInput
                v-model="password"
                :type="reveal ? 'text' : 'password'"
                :autocomplete="screen === 'login' ? 'current-password' : 'new-password'"
                :minlength="screen === 'login' ? undefined : 8"
                maxlength="1024"
                size="lg"
                :disabled="busy"
                required
              >
                <template #suffix>
                  <UiButton
                    variant="ghost"
                    size="sm"
                    :aria-label="reveal ? 'Hide password' : 'Show password'"
                    :aria-pressed="reveal"
                    :icon-left="reveal ? 'eye-off' : 'eye'"
                    :disabled="busy"
                    @click="reveal = !reveal"
                  />
                </template>
              </UiInput>
            </UiFormField>
          </div>
          <p
            v-if="screen === 'check-email' || ((!needsEmail && !needsPassword) && error) || feedback"
            id="account-error"
            :class="[error ? 'text-danger-fg' : 'text-fg-muted', 'text-center text-sm', screen === 'check-email' && 'min-h-5']"
            :role="error ? 'alert' : 'status'"
            tabindex="-1"
          >
            {{ error || feedback }}
          </p>
          <UiButton
            type="submit"
            :variant="screen === 'check-email' ? 'secondary' : 'primary'"
            size="lg"
            class="w-full"
            :loading="busy"
          >
            {{ primary }}
          </UiButton>
        </form>
        <div class="mt-5 flex flex-col items-center gap-2 text-center text-sm text-fg-muted">
          <p v-if="screen === 'login'">
            New to StackOS? <UiButton
              variant="link"
              :disabled="busy"
              @click="choose('register')"
            >
              Create an account
            </UiButton>
          </p>
          <p v-else-if="screen === 'register'">
            Already have an account? <UiButton
              variant="link"
              :disabled="busy"
              @click="choose('login')"
            >
              Sign in
            </UiButton>
          </p>
          <template v-else-if="['verified', 'updated'].includes(screen) && desktopReturn">
            <p>If it does not open, launch StackOS from Applications.</p>
            <UiButton
              variant="link"
              @click="continueBrowser"
            >
              Continue in this browser
            </UiButton>
          </template>
          <template v-else-if="!['verified', 'updated', 'password'].includes(screen)">
            <UiButton
              v-if="screen === 'check-email'"
              variant="link"
              :disabled="busy"
              @click="choose(purpose === 'recovery' ? 'recover' : 'register')"
            >
              Use a different email
            </UiButton>
            <UiButton
              variant="link"
              :disabled="busy"
              @click="choose('login')"
            >
              Back to sign in
            </UiButton>
          </template>
          <UiButton
            v-if="screen === 'login' && error"
            variant="link"
            :disabled="busy"
            @click="purpose = 'signup'; choose('link-error')"
          >
            Resend verification email
          </UiButton>
        </div>
      </template>
    </section>
  </main>
</template>

<style scoped>
/* Headings receive programmatic focus for screen-reader announcements, never
   sequential keyboard focus. Interactive controls retain the shared rings. */
h1:focus { outline: none; }
</style>
