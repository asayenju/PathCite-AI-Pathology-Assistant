<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert } from 'lucide-vue-next'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { Status } from '@/composables/usePredict'
import { CLASS_NAMES, type ClassCode } from '@/lib/classes'
import type { PredictResponse } from '@/types'
import ClassBars from './ClassBars.vue'
import ScoreRuler from './ScoreRuler.vue'

const props = defineProps<{
  status: Status
  result: PredictResponse | null
  errorMessage: string
  /** True class of the slide on stage, when it is a bundled slide. */
  slideLabel: ClassCode | null
}>()

defineEmits<{ clear: [] }>()

const matches = computed(() => props.slideLabel !== null && props.result?.tissue === props.slideLabel)
</script>

<template>
  <section aria-labelledby="reading-title" class="min-h-[28rem]">
    <h2 id="reading-title" class="mb-3 text-sm font-semibold text-quiet">Reading</h2>

    <div aria-live="polite" :aria-busy="status === 'loading'">
      <!-- Nothing on the stage -->
      <div v-if="status === 'idle'" class="max-w-[38ch]">
        <p class="cond text-4xl leading-tight font-semibold tracking-tight text-ink/80">Put a slide on the stage.</p>
        <p class="mt-4 text-quiet">
          The model looks at one colorectal H&amp;E patch and reports its best match among 9 tissue types, with a score.
        </p>
      </div>

      <!-- Waiting for the model -->
      <div v-else-if="status === 'loading'" class="space-y-6" role="status">
        <span class="sr-only">Reading the slide</span>
        <Skeleton class="h-16 w-3/4 bg-frost" />
        <Skeleton class="h-10 w-1/2 bg-frost" />
        <Skeleton class="h-32 w-full bg-frost" />
      </div>

      <!-- Something went wrong: say what and what to do -->
      <Alert v-else-if="status === 'error'" class="max-w-[48ch] border-eosin bg-glass text-ink">
        <CircleAlert class="text-eosin" />
        <AlertTitle>The slide wasn't read</AlertTitle>
        <AlertDescription class="text-ink">
          <p>{{ errorMessage }}</p>
          <Button variant="outline" size="sm" class="mt-3" @click="$emit('clear')">Clear stage</Button>
        </AlertDescription>
      </Alert>

      <!-- The reading -->
      <div v-else-if="result" class="reading-in space-y-7">
        <div>
          <h3
            class="cond text-[clamp(2.75rem,5.2vw,4.75rem)] leading-[0.95] font-semibold tracking-tight [text-wrap:balance]"
          >
            {{ CLASS_NAMES[result.tissue] }}
          </h3>
          <p class="mt-3 text-lg">
            <span class="cond font-bold tracking-wide">{{ result.tissue }}</span>
            <span class="text-quiet"> is the best match of 9 tissue types.</span>
          </p>
        </div>

        <div class="max-w-[34rem]">
          <ScoreRuler :value="result.confidence" :uncertain="result.uncertain" />
          <p v-if="result.uncertain" class="mt-3 flex items-center gap-2 text-sm">
            <span class="size-2.5 shrink-0 rounded-full bg-amber" aria-hidden="true" />
            Low score. Check this one by eye.
          </p>
        </div>

        <p v-if="slideLabel" class="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span>
            Slide label <span class="cond font-bold tracking-wide">{{ slideLabel }}</span>
          </span>
          <Badge
            v-if="matches"
            class="rounded-glass bg-ink px-2 py-0.5 text-sm text-glass"
          >
            Matches
          </Badge>
          <Badge v-else variant="outline" class="gap-1.5 rounded-glass border-eosin bg-glass px-2 py-0.5 text-sm text-ink">
            <span class="size-2 rounded-full bg-eosin" aria-hidden="true" />
            Differs from label
          </Badge>
        </p>

        <div class="max-w-[34rem]">
          <h3 class="mb-2 text-sm font-semibold text-quiet">All 9 tissue types</h3>
          <ClassBars :probabilities="result.probabilities" :predicted="result.tissue" />
        </div>

        <Alert v-if="result.warnings.length" class="max-w-[44ch] border-amber bg-glass text-ink">
          <CircleAlert class="text-amber" />
          <AlertTitle>Check the input</AlertTitle>
          <AlertDescription class="text-ink">
            <p v-for="warning in result.warnings" :key="warning">{{ warning }}</p>
          </AlertDescription>
        </Alert>

        <p class="max-w-[48ch] text-sm text-quiet">
          {{ result.notice }}
          <span class="tnum">Model {{ result.model_version }}.</span>
        </p>
      </div>
    </div>
  </section>
</template>
