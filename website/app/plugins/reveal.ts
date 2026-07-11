export default defineNuxtPlugin((nuxtApp) => {
  if (import.meta.server) {
    nuxtApp.vueApp.directive('reveal', {
      getSSRProps(binding) {
        const delay = Number(binding.value ?? 0)
        return {
          class: 'reveal',
          style: `--reveal-delay: ${delay}ms`,
        }
      },
    })
    return
  }

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  document.documentElement.classList.add('has-js')

  nuxtApp.vueApp.directive<HTMLElement>('reveal', {
    mounted(element, binding) {
      const delay = Number(binding.value ?? 0)
      element.classList.add('reveal')
      element.style.setProperty('--reveal-delay', `${delay}ms`)

      if (reduceMotion || !('IntersectionObserver' in window)) {
        element.classList.add('is-visible')
        return
      }

      const observer = new IntersectionObserver(
        (entries) => {
          if (!entries.some((entry) => entry.isIntersecting)) return
          element.classList.add('is-visible')
          observer.disconnect()
        },
        { threshold: 0.14 },
      )

      observer.observe(element)
    },
  })
})
