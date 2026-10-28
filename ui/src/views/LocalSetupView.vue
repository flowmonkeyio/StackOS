<script setup lang="ts">
import BackupPanel from '@/components/backups/BackupPanel.vue'
import { UiPageHeader, UiPageShell } from '@/components/ui'
</script>

<template>
  <UiPageShell>
    <UiPageHeader
      title="Backups"
      description="Backup settings and coverage for this StackOS installation across all projects."
    />
    <BackupPanel />
  </UiPageShell>
</template>
