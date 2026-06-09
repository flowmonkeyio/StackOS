<script setup lang="ts">
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiSectionHeader,
  UiSkeleton,
} from '@/components/ui'

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
  <section
    class="space-y-3"
    aria-label="Communication setup"
  >
    <UiSectionHeader
      title="Communication setup"
      description="Provider-neutral profiles, named destinations, and public ingress routes used by agents."
      as="h3"
    >
      <template #actions>
        <UiBadge
          :tone="ingressStatus?.ready ? 'success' : 'warning'"
          :dot="ingressStatus?.ready === true"
        >
          {{ ingressStatus?.ready ? 'ingress ready' : 'ingress pending' }}
        </UiBadge>
        <UiButton
          size="sm"
          variant="secondary"
          icon-left="refresh"
          :loading="loading"
          @click="$emit('refresh')"
        >
          Refresh
        </UiButton>
      </template>
    </UiSectionHeader>

    <UiCallout
      v-if="message"
      :tone="message.tone"
    >
      {{ message.text }}
    </UiCallout>

    <div
      v-if="loading"
      class="grid gap-4 lg:grid-cols-2 2xl:grid-cols-4"
      aria-label="Loading communication setup"
    >
      <UiCard
        v-for="n in 4"
        :key="n"
      >
        <UiSkeleton
          shape="line"
          :lines="3"
        />
      </UiCard>
    </div>

    <div
      v-else
      class="grid items-start gap-4 lg:grid-cols-2 2xl:grid-cols-4"
    >
      <UiCard
        section
        aria-label="Communication profiles"
      >
        <template #header>
          <h4 class="t-h3 text-fg-strong">
            Profiles
          </h4>
          <UiBadge>{{ profiles.length }}</UiBadge>
        </template>
        <UiEmptyState
          v-if="profiles.length === 0"
          size="sm"
          icon="users"
          title="No profiles configured"
          description="Profiles bind provider identities to policy. Agents and operators register them through StackOS operations."
        />
        <ul
          v-else
          class="max-h-[34rem] divide-y divide-border-subtle overflow-y-auto"
        >
          <li
            v-for="profile in profiles"
            :key="profile.profile_ref"
            class="py-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h5 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                {{ communicationProfileTitle(profile) }}
              </h5>
              <UiBadge :tone="profile.enabled ? 'success' : 'warning'">
                {{ profile.enabled ? 'enabled' : 'disabled' }}
              </UiBadge>
            </div>
            <p class="mt-0.5 truncate font-mono text-2xs text-fg-subtle">
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
      </UiCard>

      <UiCard
        section
        aria-label="Communication surfaces"
      >
        <template #header>
          <h4 class="t-h3 text-fg-strong">
            Surfaces
          </h4>
          <UiBadge>{{ surfaces.length }}</UiBadge>
        </template>
        <UiEmptyState
          v-if="surfaces.length === 0"
          size="sm"
          icon="megaphone"
          title="No surfaces configured"
          description="Surfaces describe where messages can be read or sent, with audience and data scope."
        />
        <ul
          v-else
          class="max-h-[34rem] divide-y divide-border-subtle overflow-y-auto"
        >
          <li
            v-for="surface in surfaces"
            :key="surface.surface_ref"
            class="py-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h5 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                {{ surfaceTitle(surface) }}
              </h5>
              <UiBadge :tone="surfaceAudienceTone(surface)">
                {{ surface.audience || 'unknown' }}
              </UiBadge>
              <UiBadge>{{ surfaceDataScope(surface) }}</UiBadge>
            </div>
            <p class="mt-0.5 truncate font-mono text-2xs text-fg-subtle">
              {{ surface.surface_ref }}
            </p>
            <p class="mt-1 line-clamp-2 text-xs text-fg-muted">
              {{ surfaceIntentSummary(surface) }}
            </p>
            <div class="mt-2 flex flex-wrap gap-1">
              <UiBadge tone="accent">
                {{ surface.provider_key }}
              </UiBadge>
              <UiBadge>{{ surface.kind }}</UiBadge>
              <UiBadge :tone="surface.send_enabled ? 'success' : 'warning'">
                {{ surface.send_enabled ? 'send enabled' : 'send disabled' }}
              </UiBadge>
            </div>
          </li>
        </ul>
      </UiCard>

      <UiCard
        section
        aria-label="Named targets"
      >
        <template #header>
          <h4 class="t-h3 text-fg-strong">
            Named targets
          </h4>
          <UiBadge>{{ targets.length }}</UiBadge>
        </template>
        <UiEmptyState
          v-if="targets.length === 0"
          size="sm"
          icon="arrow-right"
          title="No named targets configured"
          description="Named targets are pre-approved send destinations agents can use without raw channel access."
        />
        <ul
          v-else
          class="max-h-[34rem] divide-y divide-border-subtle overflow-y-auto"
        >
          <li
            v-for="target in targets"
            :key="target.target_ref"
            class="py-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h5 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                {{ targetTitle(target) }}
              </h5>
              <UiBadge :tone="target.enabled ? 'success' : 'warning'">
                {{ target.enabled ? 'enabled' : 'disabled' }}
              </UiBadge>
              <UiBadge>{{ targetPolicySummary(target) }}</UiBadge>
            </div>
            <p class="mt-0.5 truncate font-mono text-2xs text-fg-subtle">
              {{ target.key }} -> {{ target.surface_ref }}
            </p>
            <p class="mt-1 truncate text-xs text-fg-muted">
              {{ target.action_ref || 'no action ref' }}
            </p>
          </li>
        </ul>
      </UiCard>

      <UiCard
        section
        aria-label="Ingress routes"
      >
        <template #header>
          <h4 class="t-h3 text-fg-strong">
            Ingress routes
          </h4>
          <UiBadge>{{ ingressStatus?.routes?.length ?? 0 }}</UiBadge>
        </template>
        <div class="py-1">
          <div class="flex flex-wrap items-center gap-1.5">
            <UiBadge
              :tone="ingressStatus?.ready ? 'success' : 'warning'"
              :dot="ingressStatus?.ready === true"
            >
              {{ ingressStatus?.endpoint?.status ?? 'not configured' }}
            </UiBadge>
            <UiBadge>{{ ingressStatus?.endpoint?.driver ?? 'no driver' }}</UiBadge>
          </div>
          <p class="mt-2 break-all font-mono text-2xs text-fg-subtle">
            {{ ingressStatus?.endpoint?.public_base_url ?? 'No public URL configured' }}
          </p>
        </div>
        <ul
          v-if="ingressStatus?.routes?.length"
          class="mt-3 divide-y divide-border-subtle border-t border-subtle"
        >
          <li
            v-for="route in ingressStatus.routes"
            :key="`${route.provider_key}:${route.profile_key}`"
            class="py-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h5 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                {{ route.profile_key }}
              </h5>
              <UiBadge tone="accent">
                {{ route.provider_key }}
              </UiBadge>
              <UiBadge :tone="routeStatusTone(route)">
                {{ route.remote_status ?? 'local' }}
              </UiBadge>
            </div>
            <p class="mt-1 break-all font-mono text-2xs text-fg-subtle">
              {{ route.ingress_url ?? route.local_url ?? '-' }}
            </p>
          </li>
        </ul>
      </UiCard>
    </div>
  </section>
</template>
