import { motion, useReducedMotion } from 'framer-motion'
import { Check, Copy } from 'lucide-react'
import { Component, useState, type ReactNode } from 'react'
import { cn } from '../lib/utils'

/** A lost WebGL context (GPU reset, sleep, driver change) throws inside three,
 *  and an uncaught error in any child unmounts the entire tree. */
export class Boundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

/** A line entering once, from 10px below. Nothing scales, nothing blurs. */
export function Reveal({
  children,
  delay = 0,
  className,
  as = 'div',
}: {
  children: ReactNode
  delay?: number
  className?: string
  as?: 'div' | 'section' | 'li' | 'tr'
}) {
  const still = useReducedMotion()
  const MotionTag = motion[as as 'div'] as typeof motion.div
  return (
    <MotionTag
      className={className}
      initial={still ? false : { opacity: 0, y: 10 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '0px 0px -10% 0px' }}
      transition={{ duration: 0.5, delay, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </MotionTag>
  )
}

export function SectionHead({
  index,
  title,
  lede,
  id,
}: {
  index: string
  title: string
  lede?: ReactNode
  id?: string
}) {
  return (
    <header className="mb-10 md:mb-14">
      <div className="flex items-center gap-4">
        <span className="num text-[11px] tracking-[0.18em] text-fog-2">{index}</span>
        <h2
          id={id}
          className="text-xl font-medium tracking-tight text-fog-0 md:text-3xl"
        >
          {title}
        </h2>
        <span className="h-px flex-1 bg-line" aria-hidden="true" />
      </div>
      {lede && (
        <p className="mt-4 max-w-[68ch] text-[15px] leading-relaxed text-fog-1">
          {lede}
        </p>
      )}
    </header>
  )
}

export function Section({
  children,
  className,
  id,
}: {
  children: ReactNode
  className?: string
  id?: string
}) {
  return (
    <section
      id={id}
      className={cn('mx-auto w-full max-w-6xl px-5 py-20 md:px-8 md:py-28', className)}
    >
      {children}
    </section>
  )
}

/** One pasteable command. The whole project is commands, so the page treats a
 *  command as the unit of trust, not as decoration. */
export function CopyLine({
  cmd,
  label,
  className,
}: {
  cmd: string
  label?: string
  className?: string
}) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cmd)
      setDone(true)
      setTimeout(() => setDone(false), 1600)
    } catch {
      setDone(false)
    }
  }
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={`Copy: ${cmd}`}
      className={cn(
        'group flex w-full items-center gap-3 border border-line bg-ink-1 px-3 py-2.5 text-left',
        'transition-colors hover:border-fog-2/60',
        className,
      )}
    >
      {label && (
        <span className="shrink-0 text-[11px] uppercase tracking-[0.14em] text-fog-2">
          {label}
        </span>
      )}
      <code className="num min-w-0 flex-1 truncate text-[13px] text-fog-0">
        <span className="text-pass">$ </span>
        {cmd}
      </code>
      <span className="shrink-0 text-fog-2 transition-colors group-hover:text-signal">
        {done ? <Check size={15} /> : <Copy size={15} />}
      </span>
    </button>
  )
}

export function Stat({
  value,
  unit,
  caption,
  emphasis,
}: {
  value: ReactNode
  unit?: string
  caption: ReactNode
  emphasis?: boolean
}) {
  return (
    <div className="border-t border-line pt-4">
      <div className="flex items-baseline gap-1.5">
        <span
          className={cn(
            'num text-3xl md:text-4xl',
            emphasis ? 'text-pass' : 'text-fog-0',
          )}
        >
          {value}
        </span>
        {unit && <span className="num text-sm text-fog-2">{unit}</span>}
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-fog-2">{caption}</p>
    </div>
  )
}

export function Chip({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: 'neutral' | 'pass' | 'refuse' | 'fail'
  className?: string
}) {
  return (
    <span
      className={cn(
        'num inline-flex items-center gap-1 border px-1.5 py-0.5 text-[11px] leading-none',
        tone === 'pass' && 'border-pass/40 text-pass',
        tone === 'refuse' && 'border-refuse/40 text-refuse',
        tone === 'fail' && 'border-fail/40 text-fail',
        tone === 'neutral' && 'border-line text-fog-2',
        className,
      )}
    >
      {children}
    </span>
  )
}
