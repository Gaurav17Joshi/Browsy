"""The floating chat panel, injected into every page over CDP.

Mounted in a Shadow DOM root so page CSS cannot reach it, and registered with
Page.addScriptToEvaluateOnNewDocument so it re-mounts on every navigation and
every new tab.

Anti-flicker: the injected script carries a *seed* -- the transcript and the
panel's size/position -- baked into its source. The daemon re-registers the
script whenever that state changes. So after a navigation the panel paints
itself already populated at document-start, instead of appearing empty and then
being repainted a round trip later.
"""
from __future__ import annotations

import asyncio
import json
import logging

from .cdp import CDP, CDPError
from .config import BUILD

log = logging.getLogger("cuaexp.panel")

BINDING = "__cuaexp_send"

# --- Browsy ------------------------------------------------------------------
# TV-head robot with two arms and a button on its chest. That button is the whole
# UI when the chat is closed: hover and Browsy points at it, click and the chat
# unfolds. States are driven by a class on the <svg>:
#   idle / think / work / done / err, plus `point` while hovered and `open`.
MASCOT_SVG = r"""
<svg class="bot idle" id="bot" viewBox="0 0 104 104" aria-hidden="true">
  <defs>
    <linearGradient id="shell" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="#3b4553"/>
      <stop offset="55%"  stop-color="#2b323d"/>
      <stop offset="100%" stop-color="#20262f"/>
    </linearGradient>
    <linearGradient id="glass" x1="0" y1="0" x2="0.35" y2="1">
      <stop offset="0%"   stop-color="#16202c"/>
      <stop offset="100%" stop-color="#080c11"/>
    </linearGradient>
    <linearGradient id="btn" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="#5d97ff"/>
      <stop offset="100%" stop-color="#2f6df5"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="50%">
      <stop offset="0%"   stop-color="#22e39a" stop-opacity=".9"/>
      <stop offset="100%" stop-color="#22e39a" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="chestglow" cx="50%" cy="50%">
      <stop offset="0%"   stop-color="#4d8dff" stop-opacity=".85"/>
      <stop offset="100%" stop-color="#4d8dff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="sheen" x1="0" y1="0" x2="0.6" y2="1">
      <stop offset="0%"   stop-color="#ffffff" stop-opacity=".2"/>
      <stop offset="60%"  stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
  </defs>

  <g class="think-icon">
    <circle class="tb" cx="15" cy="13" r="9.6"/>
    <text class="tb-q"    x="15" y="17.6">?</text>
    <text class="tb-bang" x="15" y="17.6">!</text>
    <g class="tb-bulb">
      <circle cx="15" cy="11.2" r="4.4" class="bulb-glass"/>
      <rect x="12.9" y="15.3" width="4.2" height="2.6" rx="1.1" class="bulb-base"/>
      <path d="M13.2 10.9a2.8 2.8 0 0 1 3.6 0" class="bulb-fil"/>
      <path d="M15 5.6v1.8M9.2 8.1l1.3 1.3M20.8 8.1l-1.3 1.3" class="bulb-ray"/>
    </g>
  </g>

  <g class="rig">
    <ellipse class="shadow" cx="52" cy="98" rx="21" ry="3.2"/>

    <g class="antenna">
      <path class="ant-stem" d="M70 15 q4 -7 8 -9"/>
      <circle class="ant-bulb-glow" cx="78.5" cy="5.5" r="9" fill="url(#glow)"/>
      <circle class="ant-bulb" cx="78.5" cy="5.5" r="3.8"/>
      <circle class="ant-spec" cx="77.3" cy="4.3" r="1.2"/>
    </g>

    <g class="arm-r">
      <path class="limb-bg" d="M74 66 L85 81 L84 94"/>
      <path class="limb"    d="M74 66 L85 81 L84 94"/>
      <circle class="joint" cx="85" cy="81" r="2.8"/>
      <circle class="mitt-bg" cx="84" cy="95" r="5.4"/>
      <circle class="mitt"    cx="84" cy="95" r="3.7"/>
    </g>

    <g class="head">
      <rect class="case"   x="18" y="10" width="68" height="46" rx="16"/>
      <rect class="sheen"  x="22" y="14" width="60" height="18" rx="12"/>
      <rect class="screen" x="25" y="17" width="54" height="32" rx="12"/>
      <g class="face">
        <g class="eyes">
          <rect class="eye" x="38" y="25" width="8" height="10.5" rx="4"/>
          <rect class="eye" x="58" y="25" width="8" height="10.5" rx="4"/>
          <g class="pupils">
            <circle cx="42" cy="30.3" r="2.1"/>
            <circle cx="62" cy="30.3" r="2.1"/>
          </g>
        </g>
        <path class="mouth"      d="M46 40.6 a6 4.8 0 0 0 12 0"/>
        <path class="mouth-flat" d="M46.6 42.4 h10.8"/>
        <g class="scanline"><rect x="25" y="17" width="54" height="3.6" rx="1.8"/></g>
      </g>
    </g>

    <rect class="neck" x="46" y="55" width="12" height="7" rx="2.6"/>

    <g class="torso">
      <rect class="body"  x="26" y="60" width="52" height="34" rx="14"/>
      <rect class="sheen" x="30" y="63" width="44" height="12" rx="8"/>
      <circle class="chest-glow" cx="52" cy="76.5" r="17" fill="url(#chestglow)"/>
      <circle class="chest-ring" cx="52" cy="76.5" r="10.9"/>
      <circle class="chest" cx="52" cy="76.5" r="9.4"/>
      <path class="sign-h" d="M47.2 76.5 h9.6"/>
      <path class="sign-v" d="M52 71.7 v9.6"/>
    </g>

    <!-- His LEFT arm reaches for the button. Drawn after the torso so the
         forearm and hand are not painted underneath it, and built from two
         line segments so `d` can be interpolated between the two poses. -->
    <g class="arm-l">
      <path class="limb-bg reach" d="M30 66 L19 81 L20 94"/>
      <path class="limb reach"    d="M30 66 L19 81 L20 94"/>
      <circle class="joint elbow" cx="19" cy="81" r="2.8"/>
      <circle class="mitt-bg hand" cx="20" cy="95" r="5.4"/>
      <circle class="mitt hand"    cx="20" cy="95" r="3.7"/>
      <circle class="tapring" cx="20" cy="95" r="7"/>
    </g>
  </g>
</svg>
"""

PANEL_CSS = r"""
:host { all: initial; }
* { box-sizing: border-box; font-family: ui-sans-serif, -apple-system, "Segoe UI", Inter, system-ui, sans-serif; }

/* ---------- the assembly ----------
   Everything is positioned relative to Browsy, not to the corner of the window:
   `wrap` sits where the robot is, and the chat unfolds out of it. That is why
   the robot can be dragged anywhere and the panel simply follows. */
.wrap { position: fixed; left: 0; top: 0; width: 0; height: 0; z-index: 1;
        transition: left .34s cubic-bezier(.2,.9,.3,1.1), top .34s cubic-bezier(.2,.9,.3,1.1); }
.wrap.drag, .wrap.sizing { transition: none; }

.p { position: absolute; width: 400px; height: 540px;
     display: flex; flex-direction: column;
     border-radius: 18px; overflow: hidden; color: #e7eaee; font-size: 13px;
     background: #14161b; border: 1px solid #2a2f38;
     box-shadow: 0 24px 70px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.03) inset;
     transform-origin: 24px 14px;
     transition: transform .22s cubic-bezier(.2,.9,.3,1.25), opacity .16s ease-out,
                 visibility 0s linear .22s; }
/* closed: folded away into the robot */
.wrap:not(.open) .p { opacity: 0; visibility: hidden; pointer-events: none;
                      transform: scale(.4); }
.wrap.open .p { opacity: 1; visibility: visible; transform: none;
                transition: transform .26s cubic-bezier(.2,.9,.3,1.25),
                            opacity .16s ease-out, visibility 0s; }
.wrap.drag .p, .wrap.sizing .p { transition: none; }
.wrap.drag, .wrap.sizing { user-select: none; }

/* ---------- floating controls (there is no title bar) ---------- */
.tools { position: absolute; right: 10px; top: 9px; z-index: 7;
         display: flex; align-items: center; gap: 6px; }
.sub { font-size: 10.5px; color: #6b7480; max-width: 130px; white-space: nowrap;
       overflow: hidden; text-overflow: ellipsis; }
.timer { font: 600 11px/1 ui-monospace, "Cascadia Code", Consolas, monospace;
         color: #7d8894; background: #1f242c; border: 1px solid #2c323b;
         padding: 4px 7px; border-radius: 6px; letter-spacing: .3px; }
.timer.run { color: #22e39a; border-color: #1f4d3c; }
.ico { background: #1b1f26; border: 1px solid #262b33; color: #7d8894; cursor: pointer;
       font-size: 14px; padding: 4px 7px; border-radius: 7px; line-height: 1; }
.ico:hover { background: #262b33; color: #e7eaee; }
.ico.stop { color: #f0836b; display: none; }

/* An answer arrived while the chat was folded away.
   NOTE the two names. The state class on the wrapper must NOT be the same as the
   class on the dot: `.wrap` would then match the dot's own rule and take its
   opacity:0 and its 13px box, and the entire robot vanishes. Which is exactly
   what happened. */
.dot { position: absolute; left: 76px; top: 14px; width: 13px; height: 13px;
       border-radius: 50%; background: #2f6df5; border: 2px solid #fff;
       box-shadow: 0 2px 6px rgba(0,0,0,.4); opacity: 0; transform: scale(.3);
       transition: opacity .2s, transform .2s cubic-bezier(.2,.9,.3,1.5);
       pointer-events: none }
.wrap.newmsg:not(.open) .dot { opacity: 1; transform: none;
                               animation: unreadpop 1.6s ease-in-out infinite }
@keyframes unreadpop { 0%,100% { transform: scale(1) } 50% { transform: scale(1.18) } }

/* ---------- the timer bubble, for when the chat is folded away ---------- */
.tbub { position: absolute; left: 104px; top: 30px; white-space: nowrap;
        font: 600 11px/1 ui-monospace, Consolas, monospace; color: #22e39a;
        background: #14161b; border: 1px solid #1f4d3c; border-radius: 8px;
        padding: 5px 8px; opacity: 0; transform: translateX(-6px) scale(.9);
        transition: opacity .18s, transform .18s; pointer-events: none; }
.wrap.busy:not(.open) .tbub { opacity: 1; transform: none; }
/* Browsy lives in the bottom-right corner by default, where a bubble hung off
   his right shoulder is simply off the screen. Put it on whichever side fits. */
.wrap.bubleft .tbub { left: auto; right: 104px; transform: translateX(6px) scale(.9); }
.wrap.bubleft.busy:not(.open) .tbub { transform: none; }

/* ---------- body ---------- */
.bd { flex: 1; overflow-y: auto; overflow-x: hidden; padding: 34px 12px 14px;
      display: flex; flex-direction: column; gap: 9px; scroll-behavior: smooth; }
.bd::-webkit-scrollbar { width: 9px }
.bd::-webkit-scrollbar-thumb { background: #2e343d; border-radius: 5px;
                               border: 2px solid #14161b }

.m { padding: 9px 12px; border-radius: 13px; line-height: 1.5; white-space: pre-wrap;
     overflow-wrap: anywhere; animation: rise .16s ease-out; }
@keyframes rise { from { opacity: 0; transform: translateY(4px) } }
.u { background: linear-gradient(180deg,#2f6df5,#2559d8); color: #fff;
     align-self: flex-end; max-width: 88%; border-bottom-right-radius: 5px; }
.a { background: #1d222a; align-self: flex-start; max-width: 95%;
     border-bottom-left-radius: 5px; border: 1px solid #262c35; }
.a code, .u code { font-family: ui-monospace, Consolas, monospace; font-size: 12px;
                   background: rgba(255,255,255,.09); padding: 1px 4px; border-radius: 4px }
.m b { font-weight: 700; color: #fff }
.a b { color: #cfe0ff }
.m a { color: #6fb0ff; text-decoration: none; border-bottom: 1px solid #2f4a75 }
.m a:hover { border-bottom-color: #6fb0ff }

.t { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 11.5px;
     color: #8b95a1; background: #191d24; border: 1px solid #242a32; border-radius: 9px;
     padding: 6px 9px; align-self: stretch; cursor: pointer; animation: rise .16s ease-out; }
.t:hover { border-color: #303743 }
.t b { color: #64d2ff; font-weight: 600 }
.t .arg { color: #6d7681 }
.t .out { display: none; color: #6f7885; margin-top: 6px; padding-top: 6px;
          border-top: 1px solid #242a32; white-space: pre-wrap; max-height: 240px;
          overflow: auto; overflow-wrap: anywhere; }
.t.open .out { display: block }
.t.err { border-color: #58302c; color: #f0a293 }
.t.err b { color: #f0836b }
.t img { max-width: 100%; border-radius: 6px; margin-top: 6px }
.t .code { margin: 0 0 6px; padding: 7px 9px; background: #0f1318; border-radius: 6px;
           border: 1px solid #212730; color: #9fb4c9; font-size: 11px; line-height: 1.5;
           white-space: pre-wrap; overflow-wrap: anywhere; max-height: 260px;
           overflow: auto; font-family: ui-monospace, "Cascadia Code", Consolas, monospace }
.t b { display: inline-block; min-width: 0 }

.chip { display: inline-flex; align-items: center; gap: 6px; background: #222833;
        border: 1px solid #2d3542; border-radius: 8px; padding: 4px 8px; font-size: 11.5px;
        color: #b8c0ca; max-width: 100% }
.chip x { cursor: pointer; color: #7d8894; font-style: normal }
.chip x:hover { color: #f0836b }

/* ---------- footer ---------- */
.grip { height: 7px; cursor: ns-resize; flex: 0 0 auto; background: #14161b;
        border-top: 1px solid #262b33; position: relative }
.grip::after { content: ''; position: absolute; left: 50%; top: 2.5px; width: 34px;
               height: 2px; margin-left: -17px; border-radius: 2px; background: #333b46 }
.grip:hover::after { background: #4a5563 }

.ft { flex: 0 0 auto; padding: 9px; background: #171a20; display: flex; flex-direction: column;
      gap: 7px }
.files { display: flex; flex-wrap: wrap; gap: 6px }
.files:empty { display: none }
.row { display: flex; gap: 7px; align-items: flex-end }
textarea { flex: 1; resize: none; height: 42px; background: #1f242c; color: #e7eaee;
           border: 1px solid #2d333d; border-radius: 10px; padding: 11px 12px; font-size: 13px;
           outline: none; line-height: 1.4; font-family: inherit }
textarea:focus { border-color: #2f6df5; background: #222834 }
textarea::placeholder { color: #626b76 }
.btn { border: none; border-radius: 10px; cursor: pointer; height: 42px; flex: 0 0 auto }
.clip { width: 38px; background: #1f242c; border: 1px solid #2d333d; color: #8b95a1; font-size: 16px }
.clip:hover { color: #e7eaee; border-color: #3b434f }
.snd { background: linear-gradient(180deg,#2f6df5,#2559d8); color: #fff; font-weight: 650;
       padding: 0 16px; font-size: 13px }
.snd:hover { filter: brightness(1.08) }
.snd:disabled { background: #2b313a; color: #6d7681; cursor: default; filter: none }

/* ---------- resize: every edge and every corner ---------- */
/* Inside the panel, not straddling its edge: `.p` clips its overflow to keep the
   rounded corners, so a handle hanging outside is invisible to hit-testing for
   exactly the half you would try to grab. */
.rz { position: absolute; z-index: 6 }
.rz.n  { left: 14px; right: 14px; top: 0; height: 8px; cursor: ns-resize }
.rz.s  { left: 14px; right: 14px; bottom: 0; height: 8px; cursor: ns-resize }
.rz.w  { top: 14px; bottom: 14px; left: 0; width: 8px; cursor: ew-resize }
.rz.e  { top: 14px; bottom: 14px; right: 0; width: 8px; cursor: ew-resize }
.rz.nw { left: 0; top: 0; width: 14px; height: 14px; cursor: nwse-resize }
.rz.ne { right: 0; top: 0; width: 14px; height: 14px; cursor: nesw-resize }
.rz.sw { left: 0; bottom: 0; width: 14px; height: 14px; cursor: nesw-resize }
.rz.se { right: 0; bottom: 0; width: 14px; height: 14px; cursor: nwse-resize }
.rz.se::after { content: ''; position: absolute; right: 3px; bottom: 3px; width: 7px;
                height: 7px; border-right: 2px solid #39414d; border-bottom: 2px solid #39414d;
                border-radius: 0 0 2px 0 }
.rz.se:hover::after { border-color: #5b6675 }

.drop { position: absolute; inset: 0; z-index: 10; display: none; align-items: center;
        justify-content: center; background: rgba(20,22,27,.93);
        border: 2px dashed #2f6df5; border-radius: 16px; font-weight: 600; color: #9db6f7 }
.p.over .drop { display: flex }

/* ---------- mascot ---------- */
/* ---------- Browsy ---------- */
.botwrap { position: absolute; left: 0; top: 0; width: 104px; height: 104px; z-index: 8;
           cursor: grab; -webkit-tap-highlight-color: transparent; }
.botwrap:active { cursor: grabbing }
.bot { width: 104px; height: 104px; overflow: visible; display: block;
       filter: drop-shadow(0 7px 16px rgba(0,0,0,.4)); }

.bot .shadow { fill: rgba(15,20,28,.28) }
.bot .case   { fill: url(#shell); stroke: #10151c; stroke-width: 2 }
.bot .body   { fill: url(#shell); stroke: #10151c; stroke-width: 2 }
.bot .sheen  { fill: url(#sheen); pointer-events: none }
.bot .screen { fill: url(#glass); stroke: #0a0e13; stroke-width: 1.4 }
.bot .neck   { fill: #29303a; stroke: #10151c; stroke-width: 2 }

.bot .eye    { fill: none; stroke: #4bd8ff; stroke-width: 2.1 }
.bot .pupils circle { fill: #4bd8ff }
.bot .mouth  { fill: none; stroke: #4bd8ff; stroke-width: 2.4; stroke-linecap: round }
.bot .mouth-flat { fill: none; stroke: #4bd8ff; stroke-width: 2.4; stroke-linecap: round;
                   opacity: 0 }
.bot .scanline rect { fill: #4bd8ff; opacity: 0 }

.bot .ant-stem { fill: none; stroke: #10151c; stroke-width: 3.4; stroke-linecap: round }
.bot .ant-bulb { fill: #22e39a; stroke: #10151c; stroke-width: 1.2 }
.bot .ant-spec { fill: #d8fff0; opacity: .8 }
.bot .ant-bulb-glow { opacity: .3 }

.bot .limb-bg { fill: none; stroke: #10151c; stroke-width: 8;
                stroke-linecap: round; stroke-linejoin: round }
.bot .limb    { fill: none; stroke: #e9edf2; stroke-width: 3.4;
                stroke-linecap: round; stroke-linejoin: round }
.bot .mitt-bg { fill: #10151c }
.bot .mitt    { fill: #e9edf2 }
/* a pivot at each elbow -- he is a robot, the bend should look like a joint */
.bot .joint   { fill: #e9edf2; stroke: #10151c; stroke-width: 1.6 }
.bot .elbow   { transition: cx .5s cubic-bezier(.32,.98,.4,1),
                            cy .5s cubic-bezier(.32,.98,.4,1) }

.bot .chest-ring { fill: none; stroke: #10151c; stroke-width: 1.8 }
.bot .chest { fill: url(#btn); stroke: #1b4ba8; stroke-width: 1.2 }
.bot .sign-h, .bot .sign-v { stroke: #fff; stroke-width: 2.8; stroke-linecap: round }
.bot .chest-glow { opacity: 0; transition: opacity .2s }

.bot .think-icon { opacity: 0 }
.bot .tb { fill: #1f242c; stroke: #39414d; stroke-width: 1.5 }
.bot .tb-q, .bot .tb-bang { font: 700 12px ui-sans-serif, system-ui; text-anchor: middle;
                            fill: #ffd166; opacity: 0 }
.bot .tb-bulb { opacity: 0 }
.bot .bulb-glass { fill: #ffd166 } .bot .bulb-base { fill: #8a7330 }
.bot .bulb-fil { fill: none; stroke: #8a6f24; stroke-width: 1 }
.bot .bulb-ray { stroke: #ffd166; stroke-width: 1.4; stroke-linecap: round; opacity: .85 }

.bot .head  { transform-origin: 52px 56px }
.bot .rig   { transform-origin: 52px 77px }
.bot .eyes  { transform-origin: 52px 30px }
.bot .torso { transform-origin: 52px 77px }
.bot .antenna { transform-origin: 70px 15px }
.bot .think-icon { transform-origin: 15px 13px }

@keyframes bob    { 0%,100% { transform: translateY(0) } 50% { transform: translateY(-2.6px) } }
@keyframes tiltLR { 0% { transform: rotate(0deg) } 22% { transform: rotate(-11deg) }
                    50% { transform: rotate(2deg) } 78% { transform: rotate(11deg) }
                    100% { transform: rotate(0deg) } }
@keyframes tiltQ  { 0%,100% { transform: rotate(-2deg) translateY(0) }
                    35% { transform: rotate(-13deg) translateY(-1.5px) }
                    70% { transform: rotate(5deg) translateY(.5px) } }
@keyframes blink  { 0%,92%,100% { transform: scaleY(1) } 96% { transform: scaleY(.08) } }
@keyframes scan   { 0%,100% { transform: translateX(-2.6px) } 50% { transform: translateX(2.6px) } }
@keyframes sweep  { 0% { transform: translateY(0); opacity: 0 } 10% { opacity: .8 }
                    85% { opacity: .8 } 100% { transform: translateY(28px); opacity: 0 } }
@keyframes pulse  { 0%,100% { opacity: .3 } 50% { opacity: .95 } }
@keyframes blip   { 0%,100% { opacity: 1 } 50% { opacity: .25 } }
@keyframes antwig { 0%,100% { transform: rotate(0deg) } 30% { transform: rotate(-9deg) }
                    65% { transform: rotate(7deg) } }
@keyframes pop    { 0% { opacity: 0; transform: translateY(5px) scale(.4) }
                    55% { opacity: 1; transform: translateY(-1px) scale(1.2) }
                    100% { opacity: 1; transform: translateY(0) scale(1) } }
@keyframes float  { 0%,100% { transform: translateY(0) } 50% { transform: translateY(-2.6px) } }
@keyframes hop    { 0% { transform: translateY(0) scale(1) }
                    22% { transform: translateY(-8px) scale(1.05) }
                    45% { transform: translateY(0) scale(.96) }
                    65% { transform: translateY(-3px) scale(1.02) }
                    100% { transform: translateY(0) scale(1) } }
@keyframes shake  { 0%,100% { transform: translateX(0) rotate(0) }
                    15% { transform: translateX(-4px) rotate(-4deg) }
                    45% { transform: translateX(4px) rotate(4deg) }
                    75% { transform: translateX(-2px) rotate(-2deg) } }
@keyframes glowup { 0%,100% { opacity: .35 } 50% { opacity: 1 } }

.bot.idle  .rig  { animation: bob 3.4s ease-in-out infinite }
.bot.idle  .eyes { animation: blink 4.2s infinite }
.bot.idle  .head { animation: tiltQ 9s ease-in-out infinite }
.bot.idle  .ant-bulb-glow { animation: pulse 3.4s ease-in-out infinite }

.bot.think .head { animation: tiltQ 1.5s ease-in-out infinite }
.bot.think .rig  { animation: bob 1.5s ease-in-out infinite }
.bot.think .eyes { animation: blink 2.4s infinite }
.bot.think .pupils { animation: scan 1.5s ease-in-out infinite }
.bot.think .think-icon { opacity: 1; animation: pop .3s ease-out,
                                                float 1.5s ease-in-out .3s infinite }
.bot.think .tb-q { opacity: 1 }
.bot.think .antenna { animation: antwig 1.5s ease-in-out infinite }
.bot.think .ant-bulb { animation: blip .9s infinite }
.bot.think .ant-bulb-glow { animation: pulse 1.4s ease-in-out infinite }

.bot.work  .head { animation: tiltLR 1.8s ease-in-out infinite }
.bot.work  .rig  { animation: bob 1.1s ease-in-out infinite }
.bot.work  .pupils { animation: scan .65s ease-in-out infinite }
.bot.work  .scanline rect { animation: sweep 1.1s linear infinite }
.bot.work  .think-icon { opacity: 1; animation: pop .3s ease-out,
                                                float 1.1s ease-in-out .3s infinite }
.bot.work  .tb-bang { opacity: 1 }
.bot.work  .antenna { animation: antwig .9s ease-in-out infinite }
.bot.work  .ant-bulb { animation: blip .45s infinite }
.bot.work  .ant-bulb-glow { animation: glowup .9s ease-in-out infinite }

.bot.done  .rig { animation: hop .85s cubic-bezier(.3,1.4,.5,1) }
.bot.done  .think-icon { opacity: 1; animation: pop .34s ease-out,
                                                float 1.6s ease-in-out .34s infinite }
.bot.done  .tb-bulb { opacity: 1 }
.bot.done  .ant-bulb-glow { opacity: .9 }
.bot.done  .mouth { d: path("M45 39.6 a7 5.6 0 0 0 14 0") }

.bot.err   .rig { animation: shake .45s ease-out }
.bot.err   .mouth { opacity: 0 } .bot.err .mouth-flat { opacity: 1 }
.bot.err   .eye, .bot.err .mouth-flat { stroke: #f0836b }
.bot.err   .pupils circle { fill: #f0836b }
.bot.err   .think-icon { opacity: 1 } .bot.err .tb-bang { opacity: 1; fill: #f0836b }

/* ---------- hover: he reaches for his own button ----------
   A morph, not a swap. The elbow and wrist interpolate along `d`, the hand is a
   circle whose cx/cy travel, and nothing fades -- so what you see is one arm
   moving, the way an arm does. Ease-out on the way there (fast off the mark,
   settling onto the button), a touch slower coming back. */
.bot .reach { transition: d .5s cubic-bezier(.32,.98,.4,1) }
.bot .hand  { transition: cx .5s cubic-bezier(.32,.98,.4,1),
                          cy .5s cubic-bezier(.32,.98,.4,1) }
.bot .tapring { fill: none; stroke: #7fb2ff; stroke-width: 2; opacity: 0;
                transition: cx .5s cubic-bezier(.32,.98,.4,1),
                            cy .5s cubic-bezier(.32,.98,.4,1) }

/* elbow tucks in, forearm comes across the body, hand lands on the button */
.bot.point .reach { d: path("M30 66 L19 85 L38.8 76.2"); }
.bot.point .hand  { cx: 38.8; cy: 76.2 }
.bot.point .elbow { cx: 19; cy: 85 }
.bot.point .tapring { cx: 42.6; cy: 75.6;
                      animation: tapring 1.5s ease-out .5s infinite }

@keyframes tapring { 0%   { r: 4.5; opacity: 0 }
                     15%  { r: 5.5; opacity: .8 }
                     75%  { r: 12; opacity: 0 }
                     100% { r: 12; opacity: 0 } }
@keyframes chestbeat { 0%,100% { transform: scale(1) } 45% { transform: scale(1.1) } }
.bot.point .chest-glow { opacity: 1; animation: chestbeat 1.4s ease-in-out infinite }
.bot.point .chest, .bot.point .chest-ring, .bot.point .sign-h, .bot.point .sign-v {
  animation: chestbeat 1.4s ease-in-out .42s infinite; transform-origin: 52px 77px }
.bot.point .head { animation: tiltQ 2.4s ease-in-out infinite }
/* he looks at what he is pointing at, and keeps pressing it */
.bot .pupils { transition: transform .4s ease-out }
.bot.point .pupils { transform: translate(-1.6px, 1.6px) }
@keyframes nudge { 0%,100% { transform: translate(0,0) }
                   45% { transform: translate(1.6px, -1.2px) } }
.bot.point .arm-l { animation: nudge 1.5s ease-in-out .5s infinite }

/* open: the plus becomes a minus, and the button goes quiet */
.bot .sign-v { transition: transform .28s cubic-bezier(.2,.9,.3,1.3), opacity .2s;
               transform-origin: 52px 77px }
.bot.open .sign-v { transform: rotate(90deg); opacity: 0 }
.bot.open .chest { fill: #39424f; stroke: #10151c }
.bot.open .sign-h { stroke: #cdd6e2 }

/* one wave the first time he appears, so the button gets noticed */
@keyframes hello { 0% { transform: none } 14% { transform: translateY(-5px) }
                   100% { transform: none } }
.bot.hello .rig { animation: hello 1.1s ease-in-out }
.bot.hello .chest-glow { opacity: 1; animation: chestbeat .6s ease-in-out 3 }
"""

PANEL_JS = r"""
(() => {
  // Every daemon process stamps its own BUILD number into this script.
  // Chrome runs document-start scripts in registration order, oldest first, and
  // a daemon we stopped leaves its registration behind inside a Chrome we then
  // reuse on the next run. With a boolean "already mounted" guard the STALE
  // copy won that race -- it painted its own empty seed and made the current
  // script return early, so the panel could be running code from a process that
  // no longer exists. Compare builds instead: an older panel is torn down and
  // replaced, a same-or-newer one is left alone, which also makes re-evaluating
  // this script inside a live page a no-op.
  const BUILD = __CUAEXP_BUILD__;
  if (window.__cuaexpBuild >= BUILD) return;
  const replacing = window.__cuaexpBuild !== undefined;
  window.__cuaexpBuild = BUILD;
  if (replacing) {
    const old = document.getElementById('__cuaexp_host');
    if (old) old.remove();
  }

  const SEED = __CUAEXP_SEED__;
  const CSS  = __CUAEXP_CSS__;
  const BOT  = __CUAEXP_BOT__;

  // Sites that enforce Trusted Types (YouTube does) make `innerHTML` throw --
  // and on YouTube even DOMParser.parseFromString is guarded. The host element
  // got created, the very next line threw, and mount() aborted leaving an empty
  // shell: no UI, no message handler, no `ready`. That is what "the chat
  // disappeared" was. A policy is still allowed, so make one and go through it.
  const TT = (() => {
    if (window.__cuaexpTT !== undefined) return window.__cuaexpTT;
    try {
      window.__cuaexpTT = (window.trustedTypes && window.trustedTypes.createPolicy)
        ? window.trustedTypes.createPolicy('cuaexp', {createHTML: s => s}) : null;
    } catch (e) { window.__cuaexpTT = null; }
    return window.__cuaexpTT;
  })();
  const setHTML = (el, html) => { el.innerHTML = TT ? TT.createHTML(html) : html; };

  let ui = Object.assign({w: 400, h: 540, inH: 42, left: null, top: null, open: false},
                         SEED.ui || {});
  const send = (o) => { try { window.__CUAEXP_BINDING__(JSON.stringify(o)); } catch (e) {} };

  const mount = () => {
    const rootEl = document.body || document.documentElement;
    if (!rootEl) return;
    // The build check has to happen HERE, not only where this script is
    // evaluated. Both scripts run at document-start, before there is a body to
    // mount into, so the mounting actually happens later -- and whichever copy
    // got there first used to win outright. In a Chrome reused from an earlier
    // run that is the OLD daemon's panel, which then keeps its own saved state
    // (it came back open, in the corner it was left in) and ignores every fix
    // made since. Stamp the host and let the newer build replace it.
    const existing = document.getElementById('__cuaexp_host');
    if (existing) {
      if (+(existing.dataset.build || 0) >= BUILD) return;
      existing.remove();
    }

    const host = document.createElement('div');
    host.id = '__cuaexp_host';
    host.dataset.build = BUILD;
    // getFullAXTree walks shadow roots, so without aria-hidden the agent sees
    // its own Send / recycle buttons as clickable refs on every page.
    host.setAttribute('aria-hidden', 'true');
    host.setAttribute('data-cuaexp-panel', '1');
    host.style.cssText = 'position:fixed;z-index:2147483647;left:0;top:0;width:0;height:0;';
    (document.documentElement || rootEl).appendChild(host);

    // Keep our input inside the panel. Events from a shadow tree are retargeted
    // to the host on the way out, so a page-level handler sees a plain <div>
    // rather than a textarea -- YouTube's player then treats Space as
    // play/pause, and the keystroke lands on the video instead of the chat box.
    // Stopping at the host is enough: our own handlers live inside the shadow
    // tree and have already run by the time the event bubbles this far.
    //
    // Our OWN window-level listeners must therefore use the CAPTURE phase --
    // capture runs on the way down, before the event reaches the host, so it
    // still fires. A bubble-phase listener on window is silently dead here, and
    // that is exactly what broke dragging: the mouseup released over the panel
    // never arrived, the drag never ended, and the panel followed the cursor
    // for ever afterwards.
    // 'focusin'/'focusout' are in this list for a specific reason: a modal with
    // a focus trap (Bootstrap's, and every library that copied it) listens for
    // focusin on document and yanks focus back the moment it lands anywhere
    // outside the modal. Retargeting means it sees our host as "outside", so it
    // fought us for focus and won -- on budget.com every character typed into
    // the chat went into the modal instead. Stopping the event here means the
    // trap never learns focus left, so there is nothing to fight.
    ['keydown', 'keyup', 'keypress', 'input', 'beforeinput', 'textInput',
     'focus', 'blur', 'focusin', 'focusout',
     'compositionstart', 'compositionupdate', 'compositionend',
     'mousedown', 'mouseup', 'mousemove', 'click', 'dblclick', 'wheel', 'contextmenu',
     'pointerdown', 'pointerup', 'pointermove', 'touchstart', 'touchmove', 'touchend',
     'dragenter', 'dragover', 'dragleave', 'drop',
     'paste', 'copy', 'cut', 'selectstart',
    ].forEach(t => host.addEventListener(t, e => e.stopPropagation()));

    const root = host.attachShadow({mode: 'open'});

    setHTML(root,
      '<style>' + CSS + '</style>' +
      '<div class="wrap" id="wrap">' +
        '<div class="p" id="p">' +
          ['n','s','e','w','ne','nw','se','sw']
            .map(d => '<div class="rz ' + d + '" data-rz="' + d + '"></div>').join('') +
          '<div class="drop">Drop files to attach</div>' +
          '<div class="tools">' +
            '<span class="sub" id="sub">ready</span>' +
            '<span class="timer" id="timer">0.0s</span>' +
            '<button class="ico" id="new" title="New context (clears the chat, keeps the browser)">&#8635;</button>' +
            '<button class="ico stop" id="stop" title="Stop">&#9632;</button>' +
          '</div>' +
          '<div class="bd" id="bd"></div>' +
          '<div class="grip" id="grip"></div>' +
          '<div class="ft">' +
            '<div class="files" id="files"></div>' +
            '<div class="row">' +
              '<button class="btn clip" id="clip" title="Attach files">&#128206;</button>' +
              '<textarea id="in" placeholder="Ask Browsy to do something..."></textarea>' +
              '<button class="btn snd" id="go">Send</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div class="botwrap" id="botwrap" title="Browsy">' + BOT +
          '<div class="tbub" id="tbub">0.0s</div>' +
          '<div class="dot" id="dot"></div>' +
        '</div>' +
      '</div>' +
      '<input type="file" id="picker" multiple style="display:none">');

    const $ = id => root.getElementById(id);
    const wrap = $('wrap'), pnl = $('p'), bd = $('bd'), ta = $('in'), go = $('go');
    const bot = $('bot'), botwrap = $('botwrap'), tbub = $('tbub');
    const sub = $('sub'), timerEl = $('timer'), filesEl = $('files');

    // Live transcript kept on `window`, not in this closure. The injected SEED is
    // frozen at document load, so a re-mount that painted from SEED would silently
    // roll the conversation back to whatever it was when the page loaded -- which
    // is exactly how the user's own question vanished while the tool cards stayed.
    if (!window.__cuaexpItems || !window.__cuaexpItems.length)
      window.__cuaexpItems = (SEED.items || []).slice();
    if (window.__cuaexpRun === undefined) window.__cuaexpRun = SEED.t0 || null;
    // The "there is an answer waiting" mark rides along in `ui`, which already
    // round-trips through the daemon and back into the injected seed. `window`
    // is not enough: the agent navigates while you are not looking, and both a
    // CSS class and a window flag die with the document.

    // ---- geometry ----------------------------------------------------------
    // Size and position are remembered across pages and across runs, so they can
    // easily describe a window that no longer exists: different monitor, resized
    // Chrome, devtools opened. Clamp on every mount and on every resize, or the
    // panel is simply gone -- parked off-screen with no way to drag it back.
    // Browsy is the anchor: ui.left/ui.top are HIS position, and the chat is laid
    // out relative to him. That is what makes "drag the robot anywhere and the
    // chat follows" fall out for free, and why opening and closing never moves
    // anything.
    const BOTW = 104, BOTH = 104;    // the robot's footprint
    const OFFX = 16, OFFY = 74;      // the chat hangs off his lower right

    const clampUI = () => {
      // While it is open the chat has to fit BELOW Browsy, not merely inside the
      // window -- otherwise a short window leaves its bottom edge, and the
      // compose box with it, hanging off the screen.
      const maxW = Math.max(300, innerWidth - OFFX - 12);
      const maxH = Math.max(220, (ui.open ? innerHeight - OFFY : innerHeight) - 12);
      ui.w   = Math.min(Math.max(300, ui.w || 400), maxW);
      ui.h   = Math.min(Math.max(220, ui.h || 540), maxH);
      ui.inH = Math.min(Math.max(42, ui.inH || 42), Math.max(42, ui.h - 150));
      if (ui.left == null) ui.left = Math.max(8, innerWidth - BOTW - 24);
      if (ui.top == null)  ui.top  = Math.max(8, innerHeight - BOTH - 24);
      ui.left = Math.min(Math.max(0, ui.left), Math.max(0, innerWidth - BOTW));
      ui.top  = Math.min(Math.max(0, ui.top),  Math.max(0, innerHeight - BOTH));
    };

    // Unfold down-right by default, but flip up or left when that would run off
    // the screen -- otherwise parking Browsy in the bottom-right corner, which is
    // exactly where he starts, would put the whole chat outside the window.
    // The chat ALWAYS hangs down and to the right, with Browsy standing on its
    // top-left corner -- that is the whole shape of the thing. It never flips.
    // If there is no room below him, HE moves: opening slides him to a spot
    // where the chat fits and he stays there. Flipping the panel above him was
    // tried and it is worse -- he ends up at the bottom of his own chat window,
    // sitting on the Send button.
    const fit = () => {
      if (!ui.open) return;
      ui.left = Math.max(0, Math.min(ui.left, innerWidth  - 10 - OFFX - ui.w));
      ui.top  = Math.max(0, Math.min(ui.top,  innerHeight - 10 - OFFY - ui.h));
    };

    const layout = () => {
      pnl.style.left = OFFX + 'px';
      pnl.style.top = OFFY + 'px';
      pnl.style.width = ui.w + 'px';
      pnl.style.height = ui.h + 'px';
      wrap.classList.toggle('bubleft', ui.left + BOTW + 70 > innerWidth);
    };

    const applyUI = () => {
      fit();
      wrap.style.left = ui.left + 'px';
      wrap.style.top = ui.top + 'px';
      ta.style.height = ui.inH + 'px';
      wrap.classList.toggle('open', !!ui.open);
      wrap.classList.toggle('newmsg', !!ui.newmsg && !ui.open);
      bot.classList.toggle('open', !!ui.open);
      layout();
    };
    const saveUI = () => send({type: 'ui', ui: ui});
    // Grow with the text up to a limit, but never below the height the user
    // dragged the grip to -- their choice wins, this only helps past it.
    const autoGrow = () => {
      ta.style.height = ui.inH + 'px';
      const want = Math.min(Math.max(ta.scrollHeight, ui.inH), 190);
      ta.style.height = want + 'px';
    };
    clampUI(); applyUI();
    // Persist what the resize did, or the daemon keeps re-seeding the old
    // off-screen geometry into the next page we navigate to.
    let rsz = null;
    addEventListener('resize', () => {
      clampUI(); applyUI();
      clearTimeout(rsz); rsz = setTimeout(saveUI, 300);
    }, true);

    // ---- rendering ---------------------------------------------------------
    const atBottom = () => bd.scrollHeight - bd.scrollTop - bd.clientHeight < 60;
    const scroll = (force) => { if (force || atBottom()) bd.scrollTop = bd.scrollHeight; };

    // The model writes markdown whether or not anyone asked it to, and raw
    // **asterisks** in the chat look like a bug. Just the three that actually
    // show up -- bold, inline code, and links -- built as DOM nodes rather than
    // HTML, so nothing a page (or a model) writes can inject markup.
    const RICH = /(\*\*[^*]+\*\*|`[^`]+`|\bhttps?:\/\/[^\s<>()]+)/g;
    const richText = (el, text) => {
      String(text).split(RICH).forEach((part, i) => {
        if (!part) return;
        if (i % 2 === 0) { el.appendChild(document.createTextNode(part)); return; }
        if (part.startsWith('**')) {
          const b = document.createElement('b');
          b.textContent = part.slice(2, -2);
          el.appendChild(b);
        } else if (part.startsWith('`')) {
          const c = document.createElement('code');
          c.textContent = part.slice(1, -1);
          el.appendChild(c);
        } else {
          const a = document.createElement('a');
          a.textContent = part.length > 60 ? part.slice(0, 57) + '\u2026' : part;
          a.href = part; a.target = '_blank'; a.rel = 'noreferrer noopener';
          el.appendChild(a);
        }
      });
    };
    const addMsg = (cls, text) => {
      const d = document.createElement('div');
      d.className = 'm ' + cls;
      richText(d, text);
      bd.appendChild(d); scroll(cls === 'u'); return d;
    };
    // Turn a raw tool call into something a person can skim.
    // Written as \u escapes on purpose: this file gets round-tripped by tooling
    // that reads UTF-8 as ANSI, which turns a literal emoji into mojibake in the
    // panel. Escapes keep the source pure ASCII and immune to that.
    const ICON = {
      snapshot: '\u{1F441}', click: '\u{1F5B1}', click_at: '\u{1F5B1}',
      fill: '\u2328', press: '\u2328', press_sequence: '\u2328',
      select_option: '\u25BE', scroll: '\u2195', navigate: '\u2192',
      go_back: '\u2190', run_js: '{ }', screenshot: '\u{1F4F7}',
      web_search: '\u{1F50E}', web_search_call: '\u{1F50E}',
      remember: '\u2605', recall: '\u2605', error: '!'};
    const describe = (name, args) => {
      let a = {};
      try { a = typeof args === 'string' ? JSON.parse(args) : (args || {}); } catch (e) {}
      const s = v => (v == null ? '' : String(v));
      switch (name) {
        case 'snapshot':   return ['Read the page', ''];
        case 'click':      return ['Clicked', s(a.ref)];
        case 'click_at':   return ['Clicked', `(${s(a.x)}, ${s(a.y)})`];
        case 'fill':       return ['Typed', `\u201C${s(a.text).slice(0, 48)}\u201D`];
        case 'press':      return ['Pressed', s(a.key)];
        case 'press_sequence':
          return ['Pressed keys', (a.keys || []).length + ' keys'];
        case 'select_option': return ['Chose', `\u201C${s(a.value)}\u201D`];
        case 'scroll':     return ['Scrolled', s(a.direction || 'down')];
        case 'navigate':   return ['Opened', s(a.url).replace(/^https?:\/\//, '').slice(0, 52)];
        case 'go_back':    return ['Went back', ''];
        case 'run_js':     return ['Ran script', ''];
        case 'screenshot': return ['Took a screenshot', ''];
        case 'web_search': case 'web_search_call':
                           return ['Searched the web', s(a.query).slice(0, 52)];
        case 'remember':   return ['Remembered', s(a.note).slice(0, 48)];
        case 'recall':     return ['Recalled notes', s(a.query)];
        default:           return [name, ''];
      }
    };

    const addTool = (name, args, out, err) => {
      const d = document.createElement('div');
      d.className = 't' + (err ? ' err' : '');
      const [title, detail] = describe(name, args);
      const b = document.createElement('b');
      b.textContent = (ICON[name] || '\u00B7') + '  ' + title;
      const a = document.createElement('span'); a.className = 'arg';
      a.textContent = detail ? '  ' + detail : '';
      const o = document.createElement('div'); o.className = 'out';

      // Code is the one argument worth showing in full, in a code block.
      let code = '';
      try { const p = typeof args === 'string' ? JSON.parse(args) : (args || {});
            code = p.code || ''; } catch (e) {}
      if (code) {
        const pre = document.createElement('pre');
        pre.className = 'code'; pre.textContent = code;
        o.appendChild(pre);
      }
      if (out) { const t = document.createElement('div'); t.textContent = out;
                 o.appendChild(t); }
      d.append(b, a, o);
      d.onclick = () => d.classList.toggle('open');
      bd.appendChild(d); scroll(); return d;
    };

    let lastTool = null, botTimer = null;
    const setBot = (state, label) => {
      // NOTE: on an SVG element `className` is a read-only SVGAnimatedString --
      // assigning to it silently does nothing. That left the mascot pinned to
      // its idle animation forever, which looked like "it only blinks".
      bot.setAttribute('class', 'bot ' + state + (ui.open ? ' open' : ''));
      // Folded away, the robot IS the status display -- the bubble beside him is
      // the only way to see that something is still running.
      wrap.classList.toggle('busy', state === 'think' || state === 'work');
      if (label != null) sub.textContent = label;
      if (state === 'done' || state === 'err') {
        clearTimeout(botTimer);
        botTimer = setTimeout(() => bot.setAttribute('class', 'bot idle'), 3200);
      }
    };

    // ---- timer -------------------------------------------------------------
    let t0 = null, tick = null;
    const fmt = ms => {
      const s = ms / 1000;
      if (s < 60) return s.toFixed(1) + 's';
      const m = Math.floor(s / 60);
      return m + 'm ' + String(Math.floor(s % 60)).padStart(2, '0') + 's';
    };
    const startTimer = (from) => {
      t0 = from || Date.now();
      window.__cuaexpRun = t0;          // survives a re-mount mid-run
      timerEl.classList.add('run');
      clearInterval(tick);
      const paint = () => { const el = fmt(Date.now() - t0);
                            timerEl.textContent = el; tbub.textContent = el; };
      paint();
      tick = setInterval(paint, 100);
    };
    const stopTimer = () => {
      clearInterval(tick); timerEl.classList.remove('run');
      if (t0) {
        const el = fmt(Date.now() - t0);
        timerEl.textContent = el;
        tbub.textContent = el;
        const item = {type: 'time', text: el};
        window.__cuaexpItems.push(item);
        addMsg('t', el);
      }
      t0 = null; window.__cuaexpRun = null;
    };

    // ---- attachments -------------------------------------------------------
    let pending = [];
    const drawFiles = () => {
      filesEl.replaceChildren();
      pending.forEach((f, i) => {
        const c = document.createElement('span');
        c.className = 'chip';
        const n = document.createElement('span');
        n.textContent = f.name + ' (' + Math.round(f.size / 1024) + ' KB)';
        const x = document.createElement('x'); x.textContent = '\u2715';
        x.onclick = () => { pending.splice(i, 1); drawFiles(); };
        c.append(n, x); filesEl.appendChild(c);
      });
    };
    const CHUNK = 180 * 1024;
    const upload = (f) => new Promise((res) => {
      const r = new FileReader();
      r.onload = () => {
        const b64 = String(r.result).split(',')[1] || '';
        const id = 'f' + Date.now() + Math.random().toString(36).slice(2, 7);
        const total = Math.max(1, Math.ceil(b64.length / CHUNK));
        for (let i = 0; i < total; i++)
          send({type: 'file_chunk', id: id, name: f.name, mime: f.type || '',
                seq: i, total: total, data: b64.slice(i * CHUNK, (i + 1) * CHUNK)});
        res(id);
      };
      r.readAsDataURL(f);
    });
    const take = (list) => {
      for (const f of list) {
        if (f.size > 12 * 1024 * 1024) { addMsg('a', 'File too large (max 12 MB): ' + f.name); continue; }
        pending.push(f);
      }
      drawFiles();
    };
    $('clip').onclick = () => $('picker').click();
    $('picker').onchange = e => { take(e.target.files); e.target.value = ''; };
    ['dragenter', 'dragover'].forEach(ev => pnl.addEventListener(ev, e => {
      e.preventDefault(); pnl.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => pnl.addEventListener(ev, e => {
      if (ev === 'dragleave' && pnl.contains(e.relatedTarget)) return;
      pnl.classList.remove('over'); }));
    pnl.addEventListener('drop', e => { e.preventDefault(); take(e.dataTransfer.files); });
    ta.addEventListener('paste', e => {
      const fs = [...(e.clipboardData?.files || [])];
      if (fs.length) { e.preventDefault(); take(fs); }
    });

    // ---- inbound -----------------------------------------------------------
    const paint = (m) => {
      if (m.type === 'user') addMsg('u', m.text);
      else if (m.type === 'assistant') addMsg('a', m.text);
      else if (m.type === 'tool') lastTool = addTool(m.name, m.args, m.out, m.err);
      else if (m.type === 'time') addMsg('t', m.text);
    };

    const remember = (m) => {
      window.__cuaexpItems.push(m);
      if (window.__cuaexpItems.length > 200) window.__cuaexpItems.shift();
    };

    window.__cuaexp_recv = (payloadStr) => {
      let m; try { m = JSON.parse(payloadStr); } catch (e) { return; }
      switch (m.type) {
        case 'user': remember(m); paint(m); setBot('think', 'thinking'); break;
        case 'assistant': remember(m); paint(m);
                          if (!ui.open) { ui.newmsg = true; applyUI(); saveUI(); }
                          break;
        case 'tool': remember(m); lastTool = addTool(m.name, m.args);
                     setBot('work', describe(m.name, m.args)[0].toLowerCase()); break;
        case 'tool_result':
          if (lastTool) {
            const o = lastTool.querySelector('.out');
            const t = document.createElement('div'); t.textContent = m.text || '';
            o.appendChild(t);
            if (m.err) lastTool.classList.add('err');
          }
          break;
        case 'shot':
          if (lastTool) { const i = document.createElement('img');
                          i.src = 'data:image/png;base64,' + m.data;
                          lastTool.querySelector('.out').appendChild(i); }
          break;
        case 'busy':
          go.disabled = !!m.on; $('stop').style.display = m.on ? '' : 'none';
          if (m.on) { if (!t0) startTimer(m.t0 || null); setBot('think', 'thinking'); }
          else { stopTimer(); setBot(m.error ? 'err' : 'done', m.error ? 'error' : 'done'); }
          break;
        case 'error': { const d = addTool('error', '', m.text, true); d.classList.add('open');
                        setBot('err', 'error'); break; }
        case 'clear': bd.replaceChildren(); window.__cuaexpItems = [];
                      addMsg('a', m.text || 'New chat.');
                      timerEl.textContent = '0.0s'; setBot('idle', 'ready'); break;
        case 'restore':
          // Authoritative repaint from the daemon. Skip when it matches what is
          // already on screen, so a routine re-mount does not flash.
          if ((m.items || []).length !== window.__cuaexpItems.length) {
            window.__cuaexpItems = (m.items || []).slice();
            bd.replaceChildren(); window.__cuaexpItems.forEach(paint); scroll(true);
          }
          break;
        case 'ui': ui = Object.assign(ui, m.ui || {}); applyUI(); break;
      }
    };

    // ---- outbound ----------------------------------------------------------
    const submit = async () => {
      const v = ta.value.trim();
      if (!v && !pending.length) return;
      const files = [];
      for (const f of pending) files.push({id: await upload(f), name: f.name,
                                           mime: f.type || '', size: f.size});
      pending = []; drawFiles();
      ta.value = ''; ta.style.height = ui.inH + 'px';
      send({type: 'message', text: v, files: files});
    };
    go.onclick = submit;
    ta.onkeydown = e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
      // Escape folds the chat away without touching the conversation -- the
      // keyboard equivalent of clicking Browsy.
      else if (e.key === 'Escape') { e.preventDefault(); setOpen(false); }
      autoGrow();
    };
    ta.addEventListener('input', autoGrow);
    $('new').onclick = () => send({type: 'reset'});
    $('stop').onclick = () => send({type: 'stop'});
    // ---- opening and closing ------------------------------------------------
    // Closing is purely visual. The conversation lives in the daemon and keeps
    // running; this hides the window and nothing else.
    const setOpen = (open) => {
      ui.open = !!open;
      if (ui.open) ui.newmsg = false;
      clampUI(); applyUI(); saveUI();
      if (ui.open) setTimeout(() => ta.focus(), 60);
    };
    // Toggle on press-then-release rather than on `click`. The agent is moving
    // the same pointer we are: a stray synthesized mouseMoved between the press
    // and the release is enough for Chrome not to compose a click at all, and
    // then Browsy just ignores you -- which is exactly what happened the first
    // time this was tried mid-task.
    let pressedOnBot = false;
    botwrap.addEventListener('mousedown', e => {
      if (e.button === 0) { pressedOnBot = true; botwrap.dataset.dragged = '0'; }
    });
    addEventListener('mouseup', () => {
      if (!pressedOnBot) return;
      pressedOnBot = false;
      if (botwrap.dataset.dragged === '1') { botwrap.dataset.dragged = '0'; return; }
      setOpen(!ui.open);
    }, true);
    // Hover makes Browsy point at his own chest button. It is the only
    // affordance there is when the chat is folded away, so it has to be obvious.
    botwrap.addEventListener('mouseenter', () => {
      if (!ui.open) bot.classList.add('point');
    });
    botwrap.addEventListener('mouseleave', () => bot.classList.remove('point'));

    // ---- drag / resize -----------------------------------------------------
    // One drag at a time, and it must be impossible to get stuck in one. Three
    // separate ways out: the mouseup (in CAPTURE phase, because the host stops
    // the bubbling copy), losing window focus, and noticing on the next move
    // that no button is held any more. The last one is the real safety net --
    // if a mouseup is ever lost, for any reason, the drag ends on the very next
    // mouse move instead of leaving the panel glued to the cursor.
    let held = null;
    const onDragMove = (e) => {
      if (!held) return;
      if (!(e.buttons & 1)) { endDrag(); return; }
      // Past a few pixels this is a drag, not a click -- so releasing does not
      // also toggle the chat open.
      if (Math.abs(e.clientX - held.s.x) + Math.abs(e.clientY - held.s.y) > 4)
        botwrap.dataset.dragged = '1';
      held.move(e, held.s);
      applyUI();
    };
    const endDrag = () => {
      if (!held) return;
      const cls = held.cls;
      held = null;
      removeEventListener('mousemove', onDragMove, true);
      removeEventListener('mouseup', endDrag, true);
      removeEventListener('blur', endDrag, true);
      wrap.classList.remove(cls);
      clampUI(); applyUI(); saveUI();
    };
    const drag = (el, cls, move) => {
      el.addEventListener('mousedown', e => {
        if (e.button !== 0 || e.target.tagName === 'BUTTON') return;
        e.preventDefault();
        endDrag();                       // never stack two drags
        const r = pnl.getBoundingClientRect();
        held = {cls: cls, move: move,
                s: {x: e.clientX, y: e.clientY, l: ui.left, t: ui.top,
                    w: r.width, h: r.height,
                    ih: parseFloat(getComputedStyle(ta).height)}};
        wrap.classList.add(cls);
        addEventListener('mousemove', onDragMove, true);
        addEventListener('mouseup', endDrag, true);
        addEventListener('blur', endDrag, true);
      });
    };
    // Browsy himself is the handle for the whole assembly.
    drag(botwrap, 'drag', (e, s) => {
      ui.left = s.l + e.clientX - s.x;
      ui.top  = s.t + e.clientY - s.y;
      clampUI();                      // cannot be dragged off the edge at all
    });

    // Every edge and every corner resizes. Pulling the top or left edge moves the
    // assembly by the same amount, so the opposite edge stays where it is --
    // which is what you expect from a window, even though the anchor is a robot.
    root.querySelectorAll('.rz').forEach(h => {
      const dir = h.dataset.rz;
      const west = dir.indexOf('w') >= 0, north = dir.indexOf('n') >= 0;
      const east = dir.indexOf('e') >= 0, south = dir.indexOf('s') >= 0;
      drag(h, 'sizing', (e, st) => {
        const dx = e.clientX - st.x, dy = e.clientY - st.y;
        if (east) ui.w = st.w + dx;
        if (west) { ui.w = st.w - dx; ui.left = st.l + dx; }
        if (south) ui.h = st.h + dy;
        if (north) { ui.h = st.h - dy; ui.top = st.t + dy; }
        clampUI();
      });
    });

    drag($('grip'), 'sizing', (e, s) => {
      ui.inH = Math.max(42, Math.min(240, s.ih - (e.clientY - s.y)));
    });

    // ---- holding on to focus ------------------------------------------------
    // Pages take focus for themselves: an autofocusing search box on load, an
    // ad frame, a modal that traps it. When that lands mid-sentence the rest of
    // what you type goes to the page instead -- silently lost, and quite
    // possibly triggering the site's own single-key shortcuts. Measured against
    // a page that grabs focus every 300ms: every one of 11 keystrokes ended up
    // on the page and the chat box stayed empty.
    //
    // So take it back -- but only while the user is actually using the box, and
    // only a few times, so this can never become a focus fight with a page.
    // Clicking anywhere outside the panel is the user choosing the page, and
    // ends the claim immediately.
    let usingBox = 0, reclaims = 0;
    const claim = () => { usingBox = Date.now(); reclaims = 0; };
    ta.addEventListener('mousedown', claim);
    ta.addEventListener('keydown', claim);
    addEventListener('focusin', e => {
      if (host.contains(e.target)) return;          // focus stayed in the panel
      if (Date.now() - usingBox > 4000 || reclaims >= 12) return;
      reclaims++;
      ta.focus();
    }, true);
    addEventListener('mousedown', e => {
      if (!host.contains(e.target)) usingBox = 0;   // the user picked the page
    }, true);

    // Some pages do not merely steal focus, they enforce it: a modal with a
    // focus trap takes it back on every single focus change, and no overlay in
    // the same document can win that fight -- a person clicking the chat box on
    // budget.com loses their typing to the site's promo modal exactly as we did.
    // So when the box is in use but focus is somewhere else, take the keystrokes
    // directly, at window capture, before the page sees them. Not a text editor:
    // enough that a sentence typed into the chat lands in the chat.
    addEventListener('keydown', e => {
      if (Date.now() - usingBox > 8000) return;
      if (root.activeElement === ta) return;         // normal path; leave it alone
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === 'Escape') { usingBox = 0; return; }
      if (e.key === 'Enter' && !e.shiftKey) { submit(); }
      else if (e.key === 'Backspace') { ta.value = ta.value.slice(0, -1); }
      else if (e.key === 'Enter') { ta.value += '\n'; }
      else if (e.key.length === 1) { ta.value += e.key; }
      else return;                                   // arrows, F-keys, modifiers
      e.preventDefault();
      e.stopPropagation();
      ta.focus();                     // if the page lets go, resume normally
    }, true);

    // ---- paint immediately, so navigation does not flash an empty panel.
    // Paint from the live store, which a re-mount preserves; SEED only seeds it
    // on a genuinely fresh document.
    window.__cuaexpItems.forEach(paint);
    scroll(true);

    // First time Browsy appears in this tab, he waves and flashes the button --
    // when the chat is folded away that button is the entire interface, so it
    // has to announce itself once.
    if (!window.__cuaexpGreeted && !ui.open) {
      window.__cuaexpGreeted = true;
      bot.classList.add('hello');
      setTimeout(() => bot.classList.remove('hello'), 2000);
    }
    if (window.__cuaexpRun) {
      go.disabled = true; $('stop').style.display = '';
      setBot('think', 'thinking');
      startTimer(window.__cuaexpRun);
    }
    send({type: 'ready', url: location.href});
  };

  // Mount as early as possible; document-start is before <body> exists.
  mount();
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', mount);
  // Some pages wipe the body out from under us (SPA route changes, document
  // rewrites). mount() is idempotent, so just keep checking.
  setInterval(mount, 1000);
})();
"""


def _build(seed: dict) -> str:
    return (PANEL_JS
            .replace("__CUAEXP_BUILD__", str(BUILD))
            .replace("__CUAEXP_BINDING__", BINDING)
            .replace("__CUAEXP_SEED__", json.dumps(seed, ensure_ascii=False))
            .replace("__CUAEXP_CSS__", json.dumps(PANEL_CSS))
            .replace("__CUAEXP_BOT__", json.dumps(MASCOT_SVG)))


class Panel:
    """Injects the panel and carries messages between it and the daemon."""

    def __init__(self, cdp: CDP, on_message):
        self.cdp = cdp
        self.on_message = on_message
        self._script_ids: list[str] = []
        self.seed: dict = {"items": [], "ui": {}, "busy": False, "t0": None}
        self._reseed_task: asyncio.Task | None = None

    # --- lifecycle ----------------------------------------------------------
    async def install(self) -> None:
        await self.cdp.page("Runtime.addBinding", {"name": BINDING})
        await self._register()
        try:
            await self.cdp.eval_js(_build(self.seed), await_promise=False)
        except CDPError as e:
            # Any JS-level error here means the panel is dead on every page, but
            # the symptom is only "no panel" -- so say so loudly rather than at
            # debug level, where a stray paren once hid for a whole round.
            # Match on "JS error" and not on SyntaxError specifically: an
            # unsubstituted placeholder throws a ReferenceError, and that hid the
            # cursor being broken until a test went looking for it.
            if "JS error" in str(e):
                log.error("PANEL SCRIPT IS BROKEN: %s", e)
            else:
                log.debug("initial panel mount: %s", e)
        self.cdp.on_event(self._on_event)
        log.info("panel installed")

    async def _register(self) -> None:
        """Replace the injected script so it carries the current seed.

        Chrome runs on-new-document scripts in registration order, so a leftover
        older copy runs FIRST. It used to win outright -- painting its stale,
        often empty seed and making the newer script return early, which is how a
        conversation could come back empty after a navigation while the daemon's
        transcript was perfectly intact. The BUILD guard demotes that to
        harmless, but the old copies still have to go or they pile up one per
        reseed.

        ADD FIRST, remove second. The other order leaves a window -- however
        short -- in which this target has NO panel registered at all, and if the
        add then fails or times out (these calls do stall mid-navigation, and
        the timeout is deliberately short) that window never closes: the panel is
        simply gone from every page that loads afterwards. This runs after every
        streamed tool call, so "rare" still means several times an hour. Measured
        on flightaware.com: the cursor script, which is registered once and never
        replaced, kept working on the very pages where the panel had vanished.
        """
        old = list(self._script_ids)
        res = await self.cdp.page("Page.addScriptToEvaluateOnNewDocument",
                                  {"source": _build(self.seed)}, timeout=6)
        if not res.get("identifier"):
            raise CDPError("addScriptToEvaluateOnNewDocument returned no identifier")
        self._script_ids = [res["identifier"]]
        # Only now is it safe to drop the previous one. Chrome runs these in
        # registration order, so a leftover older copy would run FIRST; the BUILD
        # guard makes that harmless, but they would otherwise pile up one per
        # reseed.
        for sid in old:
            try:
                await self.cdp.page("Page.removeScriptToEvaluateOnNewDocument",
                                    {"identifier": sid}, timeout=6)
            except CDPError:
                pass

    async def reinstall_for_session(self) -> None:
        """Called after attaching to a different tab.

        Goes through _register so earlier registrations are *removed*, not just
        forgotten. Forgetting them orphans a script that still runs -- and since
        it was registered first, it wins the mount race and paints its stale
        seed. If the ids belong to a since-closed session the removal simply
        fails, which is fine.
        """
        # Each step independently: a failure in one is no reason to skip the
        # rest, and re-registering matters even if the immediate mount fails.
        for step in (
            lambda: self.cdp.page("Runtime.addBinding", {"name": BINDING}),
            self._register,
            lambda: self.cdp.eval_js(_build(self.seed), await_promise=False),
        ):
            try:
                await step()
            except CDPError as e:
                log.warning("panel reinstall step failed: %s", str(e)[:120])

    # --- seed (anti-flicker) ------------------------------------------------
    def update_seed(self, items: list[dict] | None = None, ui: dict | None = None,
                    busy: bool | None = None, t0: float | None = ...) -> None:
        if items is not None:
            self.seed["items"] = items[-60:]
        if ui is not None:
            self.seed["ui"] = ui
        if busy is not None:
            self.seed["busy"] = busy
        if t0 is not ...:
            self.seed["t0"] = t0
        if self._reseed_task and not self._reseed_task.done():
            return
        self._reseed_task = asyncio.create_task(self._reseed_soon())

    async def _reseed_soon(self) -> None:
        await asyncio.sleep(0.25)         # debounce: re-registering is cheap but not free
        try:
            await self._register()
        except Exception as e:
            # Never let this die silently. It is not fatal any more -- the
            # previous registration is still live, because we add before we
            # remove -- but it does mean the panel is repainting a stale seed.
            log.warning("panel reseed failed (keeping the previous script): %s",
                        str(e)[:120])

    # --- messaging ----------------------------------------------------------
    def _on_event(self, method: str, params: dict, sess: str | None):
        if method == "Runtime.bindingCalled" and params.get("name") == BINDING:
            try:
                msg = json.loads(params.get("payload") or "{}")
            except Exception:
                return
            return self.on_message(msg)

    async def push(self, obj: dict) -> None:
        payload = json.dumps(obj, ensure_ascii=False)
        js = f"window.__cuaexp_recv && window.__cuaexp_recv({json.dumps(payload)})"
        try:
            await self.cdp.eval_js(js, await_promise=False, timeout=8)
        except CDPError:
            pass
