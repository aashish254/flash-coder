let cached: boolean | null = null

/** Whether this machine should get the rotating scene at all.
 *
 *  Three gates, each one a real reader rather than a test convenience:
 *  - no context at all (remote desktop, locked-down browser, old driver).
 *    three.js throws here instead of degrading, and an uncaught error in a
 *    child unmounts the whole React tree — the page would be blank.
 *  - a *software* context. SwiftShader renders this scene at a few frames a
 *    minute and burns the CPU doing it, which is worse than the flat graph.
 *  - `prefers-reduced-motion`. A scene whose entire behaviour is rotation
 *    should not be mounted for a reader who asked not to have things spin. */
export function wantsCanvas(): boolean {
  if (cached !== null) return cached
  cached = (() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false
    try {
      const canvas = document.createElement('canvas')
      const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
      if (!gl) return false
      const info = gl.getExtension('WEBGL_debug_renderer_info')
      const renderer = info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : ''
      // Release it: a page should not hold two live contexts to ask one question.
      gl.getExtension('WEBGL_lose_context')?.loseContext()
      return !/swiftshader|software|basic render/i.test(renderer)
    } catch {
      return false
    }
  })()
  return cached
}
