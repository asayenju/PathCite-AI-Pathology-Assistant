<script setup lang="ts">
import { computed } from 'vue'
import CheckSourceItem from './CheckSourceItem.vue'
import { STANCE_GROUPS } from '@/lib/check'
import type { CheckSource } from '@/types'

/**
 * The sources of a checked claim (docs/PRD-rag-checker.md), grouped Supports / Contradicts / Unclear (empty groups
 * left out), with the irrelevant ones collapsed under "Not relevant (n)".
 */
const props = defineProps<{ sources: CheckSource[] }>()

const groups = computed(() =>
  STANCE_GROUPS.map((g) => ({ ...g, items: props.sources.filter((s) => s.stance === g.stance) })).filter(
    (g) => g.items.length > 0,
  ),
)
const irrelevant = computed(() => props.sources.filter((s) => s.stance === 'irrelevant'))
</script>

<template>
  <section class="space-y-2" data-testid="check-sources" aria-label="Sources">
    <p class="text-xs font-semibold text-quiet">Sources</p>
    <p v-if="sources.length === 0" class="text-sm text-quiet" data-testid="sources-empty">
      No passages were found in the corpus.
    </p>
    <p v-else-if="groups.length === 0" class="text-sm text-quiet" data-testid="sources-empty">
      None of the passages found are about this claim.
    </p>

    <div v-for="g in groups" :key="g.stance" class="space-y-1.5" :data-testid="`sources-${g.stance}`">
      <h4 class="text-xs font-semibold text-ink">{{ g.label }} ({{ g.items.length }})</h4>
      <ul class="space-y-1.5">
        <li v-for="s in g.items" :key="s.chunk_id"><CheckSourceItem :source="s" /></li>
      </ul>
    </div>

    <details v-if="irrelevant.length" data-testid="sources-irrelevant">
      <summary class="cursor-pointer text-xs font-semibold text-quiet">Not relevant ({{ irrelevant.length }})</summary>
      <ul class="mt-1.5 space-y-1.5">
        <li v-for="s in irrelevant" :key="s.chunk_id"><CheckSourceItem :source="s" /></li>
      </ul>
    </details>
  </section>
</template>
