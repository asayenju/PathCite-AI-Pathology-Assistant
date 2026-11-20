<script setup lang="ts">
import { computed } from 'vue'
import { CLASS_NAMES, type ClassCode } from '@/lib/classes'

const props = defineProps<{ probabilities: Record<ClassCode, number>; predicted: ClassCode }>()

const bars = computed(() =>
  (Object.entries(props.probabilities) as [ClassCode, number][])
    .map(([code, value]) => ({ code, value }))
    .sort((a, b) => b.value - a.value),
)

const percent = (value: number) => (value < 0.005 && value > 0 ? '<1%' : `${Math.round(value * 100)}%`)
</script>

<template>
  <ul class="flex h-32 items-end gap-1.5" aria-label="Model score for each of the 9 tissue types, highest first">
    <li
      v-for="bar in bars"
      :key="bar.code"
      class="flex h-full min-w-0 flex-1 flex-col justify-end"
      :aria-label="`${CLASS_NAMES[bar.code]}, ${percent(bar.value)}`"
    >
      <span class="tnum mb-1 h-4 text-center text-[0.7rem] leading-4 text-quiet">
        {{ bar.value >= 0.03 || bar.code === predicted ? percent(bar.value) : '' }}
      </span>
      <span
        class="block w-full rounded-t-[1px]"
        :class="bar.code === predicted ? 'bg-eosin' : 'bg-ink/30'"
        :style="{ height: `${Math.max(bar.value * 88, 2)}px` }"
      />
      <span class="cond mt-1.5 h-4 text-center text-xs leading-4 font-semibold" :class="bar.code !== predicted && 'text-quiet'">
        {{ bar.code }}
      </span>
    </li>
  </ul>
</template>
