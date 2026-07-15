export function useDownloadUrl() {
  const config = useRuntimeConfig()
  const downloadUrl = config.public.downloadUrl as string | undefined

  if (!downloadUrl) {
    throw new Error('Download URL is not configured')
  }

  return downloadUrl
}
