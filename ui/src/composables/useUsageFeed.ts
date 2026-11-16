import { inject, type InjectionKey, type Ref } from 'vue'
import type { UsageFeedState } from '@/lib/usageUpdates'

// Presentation reads the existing page subscription; cards never open a stream.
export const usageFeedKey: InjectionKey<Readonly<Ref<UsageFeedState>>> = Symbol('usage-feed')

export function useUsageFeed() {
  return inject(usageFeedKey, null)
}
