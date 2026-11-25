<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue"
import type { GoalStyle } from "../services/personalClient"

const props = defineProps<{ styleName: GoalStyle; percent: number }>()
const canvas = ref<HTMLCanvasElement>()
const width = 440
const height = 210
let observer: ResizeObserver | undefined
let animation = 0
let level = 0

type Point = [number, number]

function sample(start: Point, curves: [Point, Point, Point][]) {
  const points = [start]
  let from = start
  for (const [a, b, end] of curves) {
    for (let i = 1; i <= 24; i++) {
      const t = i / 24
      const s = 1 - t
      points.push([
        s ** 3 * from[0] + 3 * s * s * t * a[0] + 3 * s * t * t * b[0] + t ** 3 * end[0],
        s ** 3 * from[1] + 3 * s * s * t * a[1] + 3 * s * t * t * b[1] + t ** 3 * end[1],
      ])
    }
    from = end
  }
  return points
}

const route = sample([34, 161], [
  [[79, 171], [76, 104], [128, 119]],
  [[161, 130], [163, 156], [196, 148]],
  [[230, 139], [231, 77], [270, 82]],
  [[309, 86], [301, 119], [340, 106]],
  [[369, 97], [370, 53], [404, 53]],
])
const stem = sample([220, 185], [
  [[202, 155], [242, 132], [226, 104]],
  [[210, 72], [244, 79], [244, 49]],
  [[244, 39], [252, 28], [264, 22]],
])

function partial(points: Point[], fraction: number): Point[] {
  const lengths = points.slice(1).map((p, i) => Math.hypot(p[0] - points[i][0], p[1] - points[i][1]))
  let remaining = lengths.reduce((sum, distance) => sum + distance, 0) * fraction
  const result = [points[0]]
  for (let i = 0; i < lengths.length && remaining > 0; i++) {
    const [x, y] = points[i]
    const [nextX, nextY] = points[i + 1]
    const t = Math.min(1, remaining / lengths[i])
    result.push([x + (nextX - x) * t, y + (nextY - y) * t])
    remaining -= lengths[i]
  }
  return result
}

function line(ctx: CanvasRenderingContext2D, points: Point[], color: string, weight: number, dash: number[] = []) {
  ctx.beginPath()
  ctx.moveTo(...points[0])
  for (const point of points.slice(1)) ctx.lineTo(...point)
  ctx.strokeStyle = color
  ctx.lineWidth = weight
  ctx.setLineDash(dash)
  ctx.stroke()
  ctx.setLineDash([])
}

function circle(ctx: CanvasRenderingContext2D, point: Point, radius: number, fill: string, stroke: string) {
  ctx.beginPath()
  ctx.arc(...point, radius, 0, Math.PI * 2)
  ctx.fillStyle = fill
  ctx.strokeStyle = stroke
  ctx.lineWidth = 2
  ctx.fill()
  ctx.stroke()
}

function drawRoute(ctx: CanvasRenderingContext2D, p: number) {
  for (const offset of [0, 12, 24]) {
    ctx.beginPath()
    ctx.moveTo(20, 77 + offset)
    ctx.bezierCurveTo(66, 91 + offset, 71, 42 + offset, 133, 43 + offset)
    ctx.bezierCurveTo(155, 44 + offset, 154, 24 + offset, 183, 17 + offset)
    ctx.moveTo(277, 194 - offset)
    ctx.bezierCurveTo(323, 190 - offset, 314, 150 - offset, 367, 162 - offset)
    ctx.bezierCurveTo(392, 174 - offset, 401, 156 - offset, 421, 150 - offset)
    ctx.strokeStyle = "#e4e9e3"
    ctx.lineWidth = 1
    ctx.stroke()
  }
  line(ctx, route, "#abb6aa", 2, [5, 6])
  const done = partial(route, p)
  if (p > 0) line(ctx, done, "#567058", 4)
  circle(ctx, route[0], 4, "#fff", "#68816a")
  circle(ctx, route[route.length - 1], 4, "#fff", "#abb6aa")
  circle(ctx, done[done.length - 1], 5, "#4b7152", "#fff")
}

function drawWater(ctx: CanvasRenderingContext2D, p: number) {
  ctx.save()
  ctx.beginPath()
  ctx.moveTo(42, 100)
  ctx.bezierCurveTo(58, 62, 87, 50, 142, 45)
  ctx.bezierCurveTo(211, 39, 244, 14, 317, 30)
  ctx.bezierCurveTo(360, 39, 399, 67, 402, 112)
  ctx.bezierCurveTo(407, 161, 376, 184, 323, 188)
  ctx.bezierCurveTo(259, 193, 196, 181, 134, 190)
  ctx.bezierCurveTo(70, 196, 23, 155, 42, 100)
  ctx.closePath()
  ctx.clip()
  ctx.fillStyle = "#ecf3f3"
  ctx.fillRect(0, 0, width, height)
  const surface = 194 - p * 170
  const amplitude = Math.min(7, p * 60, (1 - p) * 60)
  if (p > 0) {
    for (const [phase, color] of [[0, "#9dcacb"], [1.5, "#70b3b4"], [3.1, "#508f96"]] as const) {
      ctx.beginPath()
      for (let x = 0; x <= width; x += 4) {
        const y = surface + Math.sin(x / 41 + phase) * amplitude
        if (x === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.lineTo(width, height)
      ctx.lineTo(0, height)
      ctx.closePath()
      const gradient = ctx.createLinearGradient(0, surface, 0, height)
      gradient.addColorStop(0, p < .99 ? color + "b9" : color)
      gradient.addColorStop(1, color + "84")
      ctx.fillStyle = gradient
      ctx.fill()
    }
    if (p > .25) {
      for (const [x, y, r] of [[141, 161, 20], [308, 145, 16]]) {
        ctx.beginPath()
        ctx.ellipse(x, Math.max(y, surface + 13), r, 4, 0, .25, Math.PI - .25)
        ctx.strokeStyle = "#ffffffb0"
        ctx.lineWidth = 1
        ctx.stroke()
      }
    }
  }
  ctx.restore()
}

function drawLeaf(ctx: CanvasRenderingContext2D, point: Point, angle: number, size: number, color: string) {
  ctx.save()
  ctx.translate(...point)
  ctx.rotate(angle)
  ctx.beginPath()
  ctx.moveTo(0, 0)
  ctx.bezierCurveTo(size * .38, -size * .7, size * .74, -size * .37, size, -size * .31)
  ctx.bezierCurveTo(size * .68, -size * .09, size * .49, size * .22, 0, 0)
  ctx.fillStyle = color
  ctx.fill()
  ctx.restore()
}

function drawPlant(ctx: CanvasRenderingContext2D, p: number) {
  line(ctx, [[137, 186], [304, 186]], "#d9e1d3", 1)
  line(ctx, stem, "#aebda9", 1.5, [4, 5])
  const grown = partial(stem, p)
  if (p > 0) line(ctx, grown, "#557552", 2.8)
  const leaves = [
    [.12, 3.26, 43], [.25, -.2, 48], [.40, 3.5, 44],
    [.55, -.16, 40], [.72, 3.95, 36], [.87, -.55, 30], [1, -.48, 22],
  ]
  for (const [point, angle, size] of leaves) {
    if (p < point) continue
    const branch = partial(stem, point)
    drawLeaf(ctx, branch[branch.length - 1], angle, size, point < .65 ? "#788f65" : "#96aa73")
  }
  if (p > 0 && p < 1) {
    const tip = grown[grown.length - 1]
    drawLeaf(ctx, tip, -1.03, 13, "#a9b781")
  }
}

function draw() {
  const el = canvas.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const ratio = Math.min(window.devicePixelRatio || 1, 2)
  const pixelWidth = Math.round(rect.width * ratio)
  const pixelHeight = Math.round(rect.height * ratio)
  if (el.width !== pixelWidth || el.height !== pixelHeight) {
    el.width = pixelWidth
    el.height = pixelHeight
  }
  const ctx = el.getContext("2d")
  if (!ctx) return
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
  ctx.clearRect(0, 0, rect.width, rect.height)
  const scale = Math.min(rect.width / width, rect.height / height)
  ctx.translate((rect.width - width * scale) / 2, (rect.height - height * scale) / 2)
  ctx.scale(scale, scale)
  ctx.lineCap = "round"
  ctx.lineJoin = "round"
  if (props.styleName === "water") drawWater(ctx, level)
  else if (props.styleName === "route") drawRoute(ctx, level)
  else drawPlant(ctx, level)
}

function update() {
  const target = Math.min(1, Math.max(0, props.percent / 100))
  window.cancelAnimationFrame(animation)
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || target === level) {
    level = target
    draw()
    return
  }
  const from = level
  const start = performance.now()
  function frame(now: number) {
    const t = Math.min(1, (now - start) / 420)
    level = from + (target - from) * (1 - (1 - t) ** 3)
    draw()
    if (t < 1) animation = window.requestAnimationFrame(frame)
  }
  animation = window.requestAnimationFrame(frame)
}

onMounted(() => {
  observer = new ResizeObserver(draw)
  if (canvas.value) observer.observe(canvas.value)
  update()
})
watch(() => [props.styleName, props.percent], update)
onUnmounted(() => {
  observer?.disconnect()
  window.cancelAnimationFrame(animation)
})
</script>

<template><canvas ref="canvas" class="goal-art" aria-hidden="true"></canvas></template>

<style scoped>
.goal-art { display: block; width: 100%; min-width: 0; aspect-ratio: 44 / 21; }
</style>
