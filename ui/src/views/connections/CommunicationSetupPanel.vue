<script setup lang="ts">
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiCountBadge,
  UiEmptyState,
  UiSectionHeader,
  UiSkeleton,
} from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'

import {
  allowedOperatorRefs,
  communicationProfileTitle,
  profileProviderKeys,
  routeStatusLabel,
  surfaceAudienceLabel,
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
        <StatusBadge
          domain="system"
          :status="ingressStatus?.ready ? 'ok' : 'degraded'"
          :label="ingressStatus?.ready ? 'Ingress ready' : 'Ingress pending'"
        />
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
        :padded="false"
        class="overflow-hidden"
        aria-label="Communication profiles"
      >
        <template #header>
          <h4 class="t-h3 text-fg-strong">
            Profiles
          </h4>
          <UiCountBadge :value="profiles.length" />
        </template>
        <UiEmptyState
          v-if="profiles.length === 0"
          size="sm"
          icon="users"
          title="No profiles configured"
          description="Profiles bind provider identities to policy. Agents and operators register them through StackOS operations."
          class="p-4"
        />
        <ul
          v-else
          class="divide-y divide-border-subtle"
        >
          <li
            v-for="profile in profiles"
            :key="profile.profile_ref"
            class="px-4 py-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h5 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                {{ communicationProfileTitle(profile) }}
              </h5>
              <StatusBadge
                domain="step"
                :status="profile.enabled ? 'enabled' : 'disabled'"
              />
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
        :padded="false"
        class="overflow-hidden"
        aria-label="Communication surfaces"
      >
        <template #header>
          <h4 class="t-h3 text-fg-strong">
            Surfaces
          </h4>
          <UiCountBadge :value="surfaces.length" />
        </template>
        <UiEmptyState
          v-if="surfaces.length === 0"
          size="sm"
          icon="megaphone"
          title="No surfaces configured"
          description="Surfaces describe where messages can be read or sent, with audience and data scope."
          class="p-4"
        />
        <ul
          v-else
          class="divide-y divide-border-subtle"
        >
          <li
            v-for="surface in surfaces"
            :key="surface.surface_ref"
            class="px-4 py-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h5 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                {{ surfaceTitle(surface) }}
              </h5>
              <UiBadge variant="outline">
                {{ surfaceAudienceLabel(surface) }}
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
              <StatusBadge
                domain="step"
                :status="surface.send_enabled ? 'enabled' : 'disabled'"
                :label="surface.send_enabled ? 'Send enabled' : 'Send disabled'"
              />
            </div>
          </li>
        </ul>
      </UiCard>

      <UiCard
        section
        :padded="false"
        class="overflow-hidden"
        aria-label="Named targets"
      >
        <template #header>
          <h4 class="t-h3 text-fg-strong">
            Named targets
          </h4>
          <UiCountBadge :value="targets.length" />
        </template>
        <UiEmptyState
          v-if="targets.length === 0"
          size="sm"
          icon="arrow-right"
          title="No named targets configured"
          description="Named targets are pre-approved send destinations agents can use without raw channel access."
          class="p-4"
        />
        <ul
          v-else
          class="divide-y divide-border-subtle"
        >
          <li
            v-for="target in targets"
            :key="target.target_ref"
            class="px-4 py-3"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h5 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                {{ targetTitle(target) }}
              </h5>
              <StatusBadge
                domain="step"
                :status="target.enabled ? 'enabled' : 'disabled'"
              />
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
          <UiCountBadge :value="ingressStatus?.routes?.length ?? 0" />
        </template>
        <div>
          <div class="flex flex-wrap items-center gap-1.5">
            <StatusBadge
              domain="system"
              :status="ingressStatus?.ready ? 'ok' : 'degraded'"
              :label="ingressStatus?.endpoint?.status ?? 'Not configured'"
            />
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
              <UiBadge variant="outline">
                {{ routeStatusLabel(route) }}
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
