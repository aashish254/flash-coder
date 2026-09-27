import { animate, useInView, useReducedMotion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'

/** Section reveals. Every number on this page is a fact about a run, so the
 *  animation is a settling, not a flourish: 0.5s, one ease, no bounce. */
export function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  return { ref, inView: useInView(ref, { once: true }) }
}

/** Count up to a printed figure. Reduced motion gets the final value at once:
 *  a number someone cannot watch animate is still the same number. */
export function useCountUp(target: number, duration = 0.9) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true })
  const still = useReducedMotion()
  const [value, setValue] = useState(still ? target : 0)

  useEffect(() => {
    if (!inView || still) {
      if (still) setValue(target)
      return
    }
    const controls = animate(0, target, {
      duration,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setValue(v),
    })
    return () => controls.stop()
  }, [inView, still, target, duration])

  return { ref, value }
}

export const EASE = [0.16, 1, 0.3, 1] as const
