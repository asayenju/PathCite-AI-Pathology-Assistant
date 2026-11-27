<script setup lang="ts">
import { computed } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import { Badge } from '@/components/ui/badge'
import { TRUST_LABELS, UNCLEAR_SOURCE_TEXT, safeUrl } from '@/lib/check'
import type { CheckSource, SourceTrust } from '@/types'

/**
 * One source of a checked claim: title (or section), section path, trust, year, quote, a note when the stance is
 * unclear (Luna's quote wasn't found in the passage), and a link that opens in a new tab (http(s) only).
 */
const props = defineProps<{ source: CheckSource }>()

/** Full class strings so Tailwind sees them. */
const TRUST_STYLES: Record<SourceTrust, string> = {
  high: 'border-moss text-moss',
  medium: 'border-hema text-hema',
  low: 'border-quiet text-quiet',
}

const href = computed(() => safeUrl(props.source.url))
</script>

<template>
  <div class="space-y-1 rounded-glass bg-bench px-2 py-1.5" data-testid="check-source" :data-stance="source.stance">
    <p class="text-sm font-medium" data-testid="source-title">{{ source.title ?? source.section }}</p>
    <p v-if="source.title && source.section" class="text-xs text-quiet" data-testid="source-section">
      {{ source.section }}
    </p>
    <p class="flex flex-wrap items-center gap-1.5">
      <Badge variant="outline" :class="TRUST_STYLES[source.trust]" data-testid="source-trust">
        {{ TRUST_LABELS[source.trust] }}
      </Badge>
      <span v-if="source.year !== null" class="text-xs text-quiet" data-testid="source-year">{{ source.year }}</span>
    </p>
    <blockquote class="border-l-2 border-frost pl-2 text-sm">“{{ source.quote }}”</blockquote>
    <p v-if="source.stance === 'unclear'" class="text-xs text-quiet" data-testid="source-opinions">
      {{ UNCLEAR_SOURCE_TEXT }}
    </p>
    <a
      v-if="href"
      :href="href"
      target="_blank"
      rel="noopener noreferrer"
      class="inline-flex items-center gap-1 text-xs text-hema underline underline-offset-2"
    >
      Open source<span class="sr-only"> (opens in a new tab)</span>
      <ExternalLink class="size-3" aria-hidden="true" />
    </a>
  </div>
</template>
