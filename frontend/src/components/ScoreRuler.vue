<script setup lang="ts">
import { computed } from 'vue'
import { UNCERTAIN_THRESHOLD } from '@/lib/api'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

const props = defineProps<{ value: number; uncertain: boolean }>()

const percent = computed(() => `${Math.min(Math.max(props.value, 0), 1) * 100}%`)
const thresholdPercent = `${UNCERTAIN_THRESHOLD * 100}%`
const ticks = Array.from({ length: 11 }, (_, i) => i / 10)
</script>

<template>
  <div>
    <div class="flex items-baseline gap-3">
      <span class="tnum cond text-4xl leading-none font-semibold tracking-tight">{{ value.toFixed(2) }}</span>
      <Tooltip>
        <TooltipTrigger as-child>
          <button type="button" class="text-sm underline decoration-dotted underline-offset-4">Model score</button>
        </TooltipTrigger>
        <TooltipContent>Not the chance of being right.</TooltipContent>
      </Tooltip>
    </div>

    <div
      class="relative mt-4 h-9"
      role="img"
      :aria-label="`Model score ${value.toFixed(2)} on a scale from 0 to 1. Scores below ${UNCERTAIN_THRESHOLD} are marked uncertain.`"
    >
      <span class="absolute inset-x-0 top-3 h-px bg-ink/40" />
      <span
        v-for="tick in ticks"
        :key="tick"
        class="absolute top-3 w-px bg-ink/40"
        :class="tick === 0 || tick === 1 ? 'h-3' : 'h-1.5'"
        :style="{ left: `${tick * 100}%` }"
      />
      <!-- Threshold: scores left of this are flagged uncertain -->
      <span class="absolute top-0 h-6 w-px bg-ink" :style="{ left: thresholdPercent }" />
      <span class="absolute top-7 -translate-x-1/2 text-[0.7rem] text-quiet tnum" :style="{ left: thresholdPercent }">
        {{ UNCERTAIN_THRESHOLD.toFixed(2) }}
      </span>
      <!-- The score -->
      <span class="absolute top-[0.6rem] h-[5px] bg-ink" :class="uncertain && 'bg-amber'" :style="{ width: percent }" />
      <span
        class="absolute top-1 h-5 w-[3px] -translate-x-1/2"
        :class="uncertain ? 'bg-amber' : 'bg-ink'"
        :style="{ left: percent }"
      />
    </div>
  </div>
</template>
