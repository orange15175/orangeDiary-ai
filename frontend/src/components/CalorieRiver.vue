<script setup>
import { computed } from "vue";
import otterImg from "../assets/otter/river-otter.png";

const props = defineProps({
  intake: { type: Number, default: 0 },
  target: { type: Number, default: 0 },
});

const progress = computed(() =>
  props.target > 0 ? Math.min(1, Math.max(0, props.intake / props.target)) : 0,
);
const position = computed(() => `${8 + progress.value * 84}%`);
const accessibleValue = computed(() => Math.round(progress.value * 100));
</script>

<template>
  <div
    class="calorie-river"
    role="progressbar"
    :aria-label="`今日热量摄入进度 ${accessibleValue}%`"
    :aria-valuemin="0"
    :aria-valuemax="100"
    :aria-valuenow="accessibleValue"
    :style="{ '--river-progress': `${accessibleValue}%`, '--otter-position': position }"
  >
    <svg class="river-water" viewBox="0 0 360 44" preserveAspectRatio="none" aria-hidden="true">
      <path class="river-bed" d="M12 10 C38 7 58 12 88 9 S140 8 170 10 S220 12 250 9 S310 8 348 11 L348 34 C320 37 290 33 260 35 S210 36 180 34 S130 32 100 35 S45 36 12 33 Q5 22 12 10Z" />
      <path class="river-current" d="M12 10 C38 7 58 12 88 9 S140 8 170 10 S220 12 250 9 S310 8 348 11 L348 34 C320 37 290 33 260 35 S210 36 180 34 S130 32 100 35 S45 36 12 33 Q5 22 12 10Z" />
      <g class="river-ripples">
        <path d="M54 18c9-3 18-3 27 0m31 8c8-3 16-3 24 0m117-9c8-2 16-2 24 0m-69 12c7-2 13-2 20 0" />
        <path d="M82 29c5-2 10-2 15 0m149-13c5-2 10-2 15 0" />
      </g>
    </svg>

    <img class="river-otter" :src="otterImg" alt="" aria-hidden="true" />

    <svg class="river-surface" viewBox="0 0 360 44" preserveAspectRatio="none" aria-hidden="true">
      <path d="M8 30c18-3 30 3 48 0s29-2 45 0m137 0c16-3 29 2 44 0s24-2 38 0" />
      <path d="M31 35c9-2 17 1 26 0m228-1c8-2 16 1 24 0" />
    </svg>
  </div>
</template>

<style scoped>
.calorie-river { position:relative; isolation:isolate; height:54px; margin:4px 0 12px; overflow:hidden; }
.river-water,.river-surface { position:absolute; inset:5px 0 0; width:100%; height:44px; overflow:visible; }
.river-bed { fill:#e7f0e9; }
.river-current { fill:#c8e1d4; clip-path:inset(0 calc(100% - var(--river-progress)) 0 0); transition:clip-path 800ms ease; }
.river-ripples,.river-surface { fill:none; stroke:#f8fcf8; stroke-width:1.4; stroke-linecap:round; }
.river-ripples { animation:current-drift 8s linear infinite; }
.river-otter { position:absolute; z-index:1; top:0; left:clamp(38px, var(--otter-position), calc(100% - 38px)); height:47px; width:auto; transform:translateX(-50%); transition:left 800ms cubic-bezier(.2,.7,.25,1); animation:otter-float 3.2s ease-in-out infinite; will-change:transform; }
.river-surface { z-index:2; pointer-events:none; }
@keyframes current-drift { to { transform:translateX(-18px); } }
@keyframes otter-float { 0%,100% { transform:translate(-50%,1px) rotate(-1.2deg); } 50% { transform:translate(-50%,-1.5px) rotate(1.2deg); } }
@media(prefers-reduced-motion:reduce) { .river-ripples,.river-otter { animation:none; } .river-current,.river-otter { transition:none; } }
</style>
