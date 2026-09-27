import { Suspense, lazy, useEffect } from 'react'
import { Footer } from './components/Footer'
import { Hero } from './components/Hero'
import { Honesty } from './components/Honesty'
import { Install } from './components/Install'
import { Loop } from './components/Loop'
import { Nav } from './components/Nav'
import { Numbers } from './components/Numbers'
import { Proof } from './components/Proof'

// recharts is the heaviest thing on the page and it is two scrolls down.
const Benchmarks = lazy(() =>
  import('./components/Benchmarks').then((m) => ({ default: m.Benchmarks })),
)

export default function App() {
  // A deep link to #honesty has to land on #honesty, but this is a client
  // render: the browser's fragment scroll runs before the section exists, and a
  // lazy panel below the fold may not be mounted yet. So the target is polled
  // for a moment and scrolled to once it is real.
  useEffect(() => {
    const id = window.location.hash.slice(1)
    if (!id) return
    const timer = window.setInterval(() => {
      const el = document.getElementById(id)
      if (!el) return
      el.scrollIntoView()
      window.clearInterval(timer)
    }, 80)
    const stop = window.setTimeout(() => window.clearInterval(timer), 4000)
    return () => {
      window.clearInterval(timer)
      window.clearTimeout(stop)
    }
  }, [])

  return (
    <div id="top" className="relative">
      <Nav />
      <main>
        <Hero />
        <Proof />
        <Numbers />
        <Suspense
          fallback={
            <div className="mx-auto w-full max-w-6xl px-5 py-28 text-[13px] text-fog-2 md:px-8">
              loading the measured panels…
            </div>
          }
        >
          <Benchmarks />
        </Suspense>
        <Loop />
        <Install />
        <Honesty />
      </main>
      <Footer />
    </div>
  )
}
