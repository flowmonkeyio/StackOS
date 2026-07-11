export function useDownloadVersion() {
  const config = useRuntimeConfig()
  const downloadVersion = config.public.downloadVersion as string | undefined

  if (!downloadVersion) {
    throw new Error('NUXT_PUBLIC_DOWNLOAD_VERSION is required')
  }

  return downloadVersion
}
