<script setup lang="ts">
import { UiBadge, UiButton, UiCallout, UiPanel, UiSectionHeader } from '@/components/ui'

import {
  allowedOperatorRefs,
  communicationProfileTitle,
  profileProviderKeys,
  routeStatusTone,
  surfaceAudienceTone,
  surfaceDataScope,
  surfaceIntentSummary,
  surfaceTitle,
  targetPolicySummary,
  targetTitle,
} from './formatters'
import type {
  CommunicationProfile,
  CommunicationSurface,
  CommunicationTarget,
  IngressEndpointStatusOut,
  MessageTone,
} from './types'

defineProps<{
  profiles: CommunicationProfile[]
  targets: CommunicationTarget[]
  surfaces: CommunicationSurface[]
  ingressStatus: IngressEndpointStatusOut | null
  loading: boolean
  message: { tone: MessageTone; text: string } | null
}>()

defineEmits<{
  (e: 'refresh'): void
}>()
</script>

<template>
  <UiPanel class="p-4">
    <UiSectionHeader
      title="Communication Setup"
      description="Provider-neutral profiles, named destinations, and public ingress routes used by agents."
    >
      <template #actions>
        <div class="flex flex-wrap items-center gap-2">
          <UiBadge>{{ profiles.length }} profiles</UiBadge>
          <UiBadge>{{ surfaces.length }} surfaces</UiBadge>
          <UiBadge>{{ targets.length }} targets</UiBadge>
          <UiBadge :tone="ingressStatus?.ready ? 'success' : 'warning'">
            {{ ingressStatus?.ready ? 'ingress ready' : 'ingress pending' }}
          </UiBadge>
          <UiButton
            size="sm"
            variant="secondary"
            icon-left="rotate-ccw"
            :loading="loading"
            @click="$emit('refresh')"
          >
            Refresh
          </UiButton>
        </div>
      </template>
    </UiSectionHeader>

    <UiCallout v-if="message" :tone="message.tone">
      {{ message.text }}
    </UiCallout>

    <div
      v-if="loading"
      class="rounded-md border border-subtle bg-bg-surface p-4 text-sm text-fg-muted"
    >
      Loading communication setup...
    </div>

    <div v-else class="grid gap-4 lg:grid-cols-2 2xl:grid-cols-4">
      <section class="min-w-0">
        <div class="mb-2 flex items-center justify-between gap-2">
          <h3 class="text-sm font-semibold text-fg-strong">Profiles</h3>
          <UiBadge>{{ profiles.length }}</UiBadge>
        </div>
        <div
          v-if="profiles.length === 0"
          class="rounded-md border border-dashed border-default p-4 text-sm text-fg-muted"
        >
          No communication profiles configured.
        </div>
        <ul v-else class="grid max-h-[34rem] gap-2 overflow-y-auto pr-1">
          <li
            v-for="profile in profiles"
            :key="profile.profile_ref"
            class="rounded-md border border-subtle bg-bg-surface-alt p-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h4 class="truncate text-sm font-semibold text-fg-strong">
                {{ communicationProfileTitle(profile) }}
              </h4>
              <UiBadge :tone="profile.enabled ? 'success' : 'warning'">
                {{ profile.enabled ? 'enabled' : 'disabled' }}
              </UiBadge>
            </div>
            <p class="mt-1 truncate font-mono text-xs text-fg-muted">
              {{ profile.profile_ref }}
            </p>
            <div class="mt-2 flex flex-wrap gap-1">
              <UiBadge
                v-for="providerKey in profileProviderKeys(profile)"
                :key="providerKey"
                tone="accent"
              >
                {{ providerKey }}
              </UiBadge>
              <UiBadge>{{ allowedOperatorRefs(profile).length }} operators</UiBadge>
            </div>
          </li>
        </ul>
      </section>

      <section class="min-w-0">
        <div class="mb-2 flex items-center justify-between gap-2">
          <h3 class="text-sm font-semibold text-fg-strong">Surfaces</h3>
          <UiBadge>{{ surfaces.length }}</UiBadge>
        </div>
        <div
          v-if="surfaces.length === 0"
          class="rounded-md border border-dashed border-default p-4 text-sm text-fg-muted"
        >
          No communication surfaces configured.
        </div>
        <ul v-else class="grid max-h-[34rem] gap-2 overflow-y-auto pr-1">
          <li
            v-for="surface in surfaces"
            :key="surface.surface_ref"
            class="rounded-md border border-subtle bg-bg-surface-alt p-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h4 class="truncate text-sm font-semibold text-fg-strong">
                {{ surfaceTitle(surface) }}
              </h4>
              <UiBadge :tone="surfaceAudienceTone(surface)">
                {{ surface.audience || 'unknown' }}
              </UiBadge>
              <UiBadge>{{ surfaceDataScope(surface) }}</UiBadge>
            </div>
            <p class="mt-1 truncate font-mono text-xs text-fg-muted">
              {{ surface.surface_ref }}
            </p>
            <p class="mt-1 line-clamp-2 text-xs text-fg-muted">
              {{ surfaceIntentSummary(surface) }}
            </p>
            <div class="mt-2 flex flex-wrap gap-1">
              <UiBadge tone="accent">{{ surface.provider_key }}</UiBadge>
              <UiBadge>{{ surface.kind }}</UiBadge>
              <UiBadge :tone="surface.send_enabled ? 'success' : 'warning'">
                {{ surface.send_enabled ? 'send enabled' : 'send disabled' }}
              </UiBadge>
            </div>
          </li>
        </ul>
      </section>

      <section class="min-w-0">
        <div class="mb-2 flex items-center justify-between gap-2">
          <h3 class="text-sm font-semibold text-fg-strong">Named Targets</h3>
          <UiBadge>{{ targets.length }}</UiBadge>
        </div>
        <div
          v-if="targets.length === 0"
          class="rounded-md border border-dashed border-default p-4 text-sm text-fg-muted"
        >
          No named targets configured.
        </div>
        <ul v-else class="grid max-h-[34rem] gap-2 overflow-y-auto pr-1">
          <li
            v-for="target in targets"
            :key="target.target_ref"
            class="rounded-md border border-subtle bg-bg-surface-alt p-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h4 class="truncate text-sm font-semibold text-fg-strong">
                {{ targetTitle(target) }}
              </h4>
              <UiBadge :tone="target.enabled ? 'success' : 'warning'">
                {{ target.enabled ? 'enabled' : 'disabled' }}
              </UiBadge>
              <UiBadge>{{ targetPolicySummary(target) }}</UiBadge>
            </div>
            <p class="mt-1 truncate font-mono text-xs text-fg-muted">
              {{ target.key }} -> {{ target.surface_ref }}
            </p>
            <p class="mt-1 truncate text-xs text-fg-muted">
              {{ target.action_ref || 'no action ref' }}
            </p>
          </li>
        </ul>
      </section>

      <section class="min-w-0">
        <div class="mb-2 flex items-center justify-between gap-2">
          <h3 class="text-sm font-semibold text-fg-strong">Ingress Routes</h3>
          <UiBadge>{{ ingressStatus?.routes?.length ?? 0 }}</UiBadge>
        </div>
        <div class="rounded-md border border-subtle bg-bg-surface-alt p-3">
          <div class="flex flex-wrap items-center gap-2">
            <UiBadge :tone="ingressStatus?.ready ? 'success' : 'warning'">
              {{ ingressStatus?.endpoint?.status ?? 'not configured' }}
            </UiBadge>
            <UiBadge>{{ ingressStatus?.endpoint?.driver ?? 'no driver' }}</UiBadge>
          </div>
          <p class="mt-2 break-all font-mono text-xs text-fg-muted">
            {{ ingressStatus?.endpoint?.public_base_url ?? 'No public URL configured' }}
          </p>
        </div>
        <ul v-if="ingressStatus?.routes?.length" class="mt-2 grid gap-2">
          <li
            v-for="route in ingressStatus.routes"
            :key="`${route.provider_key}:${route.profile_key}`"
            class="min-w-0 rounded-md border border-subtle bg-bg-surface-alt p-3"
          >
            <div class="flex flex-wrap items-center gap-2">
              <h4 class="text-sm font-semibold text-fg-strong">{{ route.profile_key }}</h4>
              <UiBadge tone="accent">{{ route.provider_key }}</UiBadge>
              <UiBadge :tone="routeStatusTone(route)">
                {{ route.remote_status ?? 'local' }}
              </UiBadge>
            </div>
            <p class="mt-1 break-all font-mono text-xs text-fg-muted">
              {{ route.ingress_url ?? route.local_url ?? '-' }}
            </p>
          </li>
        </ul>
      </section>
    </div>
  </UiPanel>
</template>
