<script setup lang="ts">
withDefaults(defineProps<{ title?: string; description?: string }>(), {
  title: 'StackOS Library',
  description: 'Complete workflows for AI-powered work.',
})
</script>

<template>
  <div :style="{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: '72px', color: '#f3f1ea', background: '#090b10', position: 'relative', overflow: 'hidden' }">
    <div :style="{ position: 'absolute', width: '520px', height: '520px', borderRadius: '260px', right: '-140px', top: '-180px', background: '#3153d6', opacity: 0.3 }" />
    <div :style="{ display: 'flex', alignItems: 'center', fontSize: '27px', fontWeight: 700 }">
      <div :style="{ width: '42px', height: '42px', display: 'flex', alignItems: 'center', justifyContent: 'center', marginRight: '16px', color: '#090b10', background: '#d9ff63', borderRadius: '12px', fontSize: '20px' }">S</div>
      StackOS <span :style="{ marginLeft: '14px', color: '#8790a0', fontSize: '18px', fontWeight: 500 }">Library</span>
    </div>
    <div :style="{ display: 'flex', flexDirection: 'column', maxWidth: '960px' }">
      <div :style="{ color: '#d9ff63', fontSize: '17px', letterSpacing: '2px', textTransform: 'uppercase', marginBottom: '22px' }">Work, organized</div>
      <div :style="{ fontSize: '64px', fontWeight: 700, lineHeight: 1.02, letterSpacing: '-3px' }">{{ title }}</div>
      <div :style="{ maxWidth: '850px', marginTop: '26px', color: '#aab1c0', fontSize: '25px', lineHeight: 1.45 }">{{ description }}</div>
    </div>
    <div :style="{ display: 'flex', alignItems: 'center', color: '#8790a0', fontSize: '17px' }">
      <div :style="{ width: '9px', height: '9px', marginRight: '10px', background: '#7ee2ad', borderRadius: '5px' }" />
      stackos.flowmonkey.io/library
    </div>
  </div>
</template>
