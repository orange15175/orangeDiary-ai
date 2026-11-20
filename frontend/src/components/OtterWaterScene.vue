<script setup>
import otter from '../assets/otter/floating.png'
</script>

<template>
  <svg class="water-scene" viewBox="0 0 390 320" aria-hidden="true">
    <defs>
      <radialGradient id="otter-water-depth"><stop stop-color="#9fcabb" stop-opacity=".42"/><stop offset="1" stop-color="#9fcabb" stop-opacity="0"/></radialGradient>
      <filter id="otter-water-blur" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="8"/></filter>
      <mask id="otter-water-mask"><image :href="otter" x="73" y="65" width="245" height="222"/></mask>
      <clipPath id="swimming-water-boundary"><rect width="390" height="320" rx="30"/></clipPath>
      <linearGradient id="swimming-water-color" x2=".8" y2="1"><stop stop-color="#b9e1d6"/><stop offset=".55" stop-color="#91cfc1"/><stop offset="1" stop-color="#c9e6db"/></linearGradient>
      <pattern id="swimming-seabed" width="390" height="420" patternUnits="userSpaceOnUse">
        <g fill="#598f85" opacity=".22"><path d="M38 32l21-8 17 15-6 19-25 3-15-13ZM285 150l27-12 23 16-7 25-31 2-18-15ZM102 322l25-9 19 15-10 19-27 1-15-14Z"/><ellipse cx="175" cy="85" rx="12" ry="7"/><ellipse cx="347" cy="328" rx="15" ry="8"/></g>
        <g fill="#bfd8a3" opacity=".6"><path d="M44 44l8-6 6 6-5 6Z M304 163l8-7 7 8-8 6Z M115 330l6-5 7 6-7 5Z"/></g>
      </pattern>
    </defs>
    <g clip-path="url(#swimming-water-boundary)">
    <rect width="390" height="320" fill="#d9eae3"/>
    <g class="seabed-scroll" opacity=".38"><rect x="-35" y="-420" width="460" height="1260" fill="url(#swimming-seabed)"/></g>
    <g class="passing-shadows" fill="#548c82" opacity=".13" filter="url(#otter-water-blur)"><ellipse cx="70" cy="70" rx="42" ry="17"/><ellipse cx="310" cy="215" rx="48" ry="23"/><ellipse cx="145" cy="420" rx="36" ry="15"/></g>
    <ellipse cx="195" cy="220" rx="157" ry="90" fill="url(#otter-water-depth)"/>
    <g class="caustics" fill="none" stroke="#fffcf6" stroke-linecap="round" stroke-width="2">
      <path d="M42 133q15-8 25 2t24-1M288 105q12 10 26 3t21 4M23 225q17 8 33-1t26 6M288 266q13-10 26-2t29-4M66 306q15-9 26-2t21-3M145 312q14-6 24-1"/>
      <path d="M55 177q10-7 22 1M318 195q9 5 15 0" stroke-width="4" opacity=".5"/>
    </g>
    <g class="swimmer-travel">
    <g class="swimmer-scale">
    <g class="underwater-shadow"><image :href="otter" x="78" y="79" width="245" height="222" opacity=".18" filter="url(#otter-water-blur)"/></g>
    <g class="outer-ripples" fill="none" stroke="#f4fff7" stroke-width="1.5"><path d="M54 238q-11 31 41 48M256 284q67-16 71-50M62 207q-8-13 6-29M318 215q8-12-4-22"/></g>
    <g class="otter-body"><image class="floating-otter" :href="otter" x="73" y="65" width="245" height="222"/>
      <path d="M78 235q72 35 142 12t100-23v85H78Z" mask="url(#otter-water-mask)" fill="#b4d7ca" opacity=".19"/>
    </g>
    <g class="contact-ripples" fill="none" stroke-linecap="round">
      <path d="M80 235q7 13 23 16M113 260q23 9 45 5M170 275q22 5 42-2M234 260q33-1 63-25" stroke="#fffdf7" stroke-width="2.4"/>
      <path d="M78 240q8 18 27 20M116 269q26 8 44 3M236 269q40-6 64-26" stroke="#8eb6a8" stroke-width="1.4" opacity=".7"/>
      <path d="M147 243q14 4 27 0M253 223q14 3 24-3" stroke="#fffdf7" stroke-width="1.5" opacity=".6"/>
    </g>
    <g class="ripple-spread ripple-spread--second" fill="none" stroke="#fffcf6" stroke-width="1.6" stroke-linecap="round"><path d="M53 254q16 25 61 31M228 294q69-5 98-42"/></g>
    <g class="ripple-spread" fill="none" stroke="#fffcf6" stroke-width="1.6" stroke-linecap="round"><path d="M53 254q16 25 61 31M228 294q69-5 98-42"/></g>
    </g>
    </g>
    <g class="water-glints" fill="none" stroke="#fffdf7" stroke-width="1.3" stroke-linecap="round"><path d="M52 190h12m-6-4v8M327 282h10m-5-3v6M133 295h8m-4-2v4"/></g>
    </g>
  </svg>
</template>

<style scoped>
.water-scene {display:block;width:100%;height:100%;overflow:visible;pointer-events:none;}
.swimmer-scale{transform:none;}
.swimmer-travel{animation:swim-course 4.2s ease-in-out infinite;transform-origin:195px 165px;}
.seabed-scroll{animation:seabed-current 20s linear infinite;}
.passing-shadows{animation:shadow-current 12s linear infinite;}
.swim-wake{animation:wake-pulse 1.4s ease-out infinite;transform-origin:195px 210px;}
.paddle-splash{animation:paddle-splash 1.4s ease-in-out infinite;transform-origin:195px 185px;}
@keyframes paddle-splash{0%,100%{opacity:.2;transform:scale(.96);}50%{opacity:.9;transform:scale(1.04);}}
@keyframes swim-course{0%,100%{transform:translate(0,0) rotate(0deg);}50%{transform:translate(10px,-6px) rotate(2deg);}}
@keyframes seabed-current{from{transform:translateY(-120px);}to{transform:translateY(300px);}}
@keyframes shadow-current{from{transform:translate(20px,-160px);}to{transform:translate(-10px,300px);}}
@keyframes wake-pulse{from{transform:translateY(-3px) scale(.96);opacity:.9;}to{transform:translateY(15px) scale(1.08);opacity:0;}}
.otter-body,.underwater-shadow {transform-origin:195px 180px;animation:water-sway 4.2s ease-in-out infinite;}
.caustics {animation:caustic-flow 6s ease-in-out infinite alternate;}
.contact-ripples {animation:water-contact 4.2s ease-in-out infinite;}
.ripple-spread {transform-origin:195px 220px;animation:ripple-expand 4.2s ease-out infinite;}
.ripple-spread--second {animation-delay:-2.1s;}
.water-glints {animation:glint-light 3.6s ease-in-out infinite alternate;}
@keyframes water-sway {0%,100%{transform:translate(0,0) rotate(0deg);}50%{transform:translate(2px,-3px) rotate(1.5deg);}}
@keyframes caustic-flow {from{transform:translate(-3px,2px);opacity:.35;}to{transform:translate(5px,-3px);opacity:.8;}}
@keyframes water-contact {0%,100%{transform:translateY(0);opacity:.85;}50%{transform:translateY(-2px);opacity:.6;}}
@keyframes ripple-expand {0%{transform:translateY(0) scale(.95,.94);opacity:0;}20%{opacity:.7;}100%{transform:translateY(26px) scale(1.18,1.14);opacity:0;}}
@keyframes glint-light {from{opacity:.25;}to{opacity:.8;}}
@media(prefers-reduced-motion:reduce){.water-scene *{animation:none!important;}.ripple-spread{opacity:.4;}.ripple-spread--second{display:none;}}
</style>
