// App entrypoint.
//
// Boots the auth store before mounting so the SPA always has a token in
// memory by the time the first view is rendered. If the bootstrap fails
// we redirect to /auth-error which lets the user retry without dropping
// into a confusing 401 cascade.

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'

import './design/colors_and_type.css'
import './style.css'
import { ApiError } from '@/lib/client'
import { registerAuthStoreWithClient, useAuthStore } from '@/stores/auth'
import { useToastsStore } from '@/stores/toasts'
import { useProjectsStore } from '@/stores/projects'

async function start(): Promise<void> {
  const app = createApp(App)
  const pinia = createPinia()
  app.use(pinia)
  const auth = useAuthStore()
  registerAuthStoreWithClient(auth)
  await auth.bootstrap()
  router.beforeEach((to) => {
    if (auth.mode === 'server' && !auth.ready && to.name !== 'sign-in') return '/sign-in'
    if (auth.ready && to.name === 'sign-in') return '/'
  })
  app.use(router)
  await router.isReady()

  const shouldResolveProjectRoute =
    router.currentRoute.value.path === '/' || router.currentRoute.value.path === '/projects'
  let projects: ReturnType<typeof useProjectsStore> | null = null
  if (auth.ready) {
    projects = useProjectsStore()
    const refresh = projects.refresh()
    if (shouldResolveProjectRoute) await refresh
    else void refresh
  }

  const toasts = useToastsStore()
  app.config.errorHandler = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 401) {
        auth.expire()
        void router.replace(auth.mode === 'server' ? '/sign-in' : '/auth-error')
      } else {
        toasts.error('Request failed', err.message)
      }
    } else if (err instanceof Error) {
      toasts.error('Unexpected error', err.message)
    }
  }

  if (!auth.ready && auth.mode !== 'server' && router.currentRoute.value.name !== 'auth-error') {
    await router.replace('/auth-error')
  }

  app.mount('#app')
}

void start()
