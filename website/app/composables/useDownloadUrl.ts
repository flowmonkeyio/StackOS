export function useDownloadUrl() {
  const config = useRuntimeConfig()
  const downloadUrl = config.public.downloadUrl as string | undefined

  if (!downloadUrl) {
    throw new Error('NUXT_PUBLIC_DOWNLOAD_URL is required')
  }

  return downloadUrl
}
