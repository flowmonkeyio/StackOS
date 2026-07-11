const CONSENT_COOKIE = 'stackos-analytics-consent'
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365

export default defineNuxtPlugin((nuxtApp) => {
  const config = useRuntimeConfig()
  const router = useRouter()
  const measurementId = config.public.gaMeasurementId as string
  const consent = useCookie<string | null>(CONSENT_COOKIE, {
    maxAge: COOKIE_MAX_AGE,
    sameSite: 'lax',
  })
  const consentState = useState<string | null>('stackos-analytics-consent', () => consent.value)

  const analyticsAllowed = computed(() => consentState.value === 'granted')
  const loadTrigger = useScriptTriggerConsent({ consent: analyticsAllowed })
  const { proxy } = useScriptGoogleAnalytics({
    id: measurementId,
    defaultConsent: {
      ad_storage: 'denied',
      ad_user_data: 'denied',
      ad_personalization: 'denied',
      analytics_storage: 'granted',
    },
    scriptOptions: {
      trigger: loadTrigger,
    },
  })

  watch(consentState, (value) => {
    consent.value = value
  }, { immediate: true })

  router.afterEach((to) => {
    if (!analyticsAllowed.value) return
    proxy.gtag('event', 'page_view', {
      page_location: window.location.href,
      page_path: to.fullPath,
      page_title: document.title,
    })
  })
})
