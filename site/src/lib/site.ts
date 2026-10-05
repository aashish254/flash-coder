/** Release-facing strings, in one place so the release commit has one line to
 *  change. `REPO_URL` is where this project is intended to be published; it is
 *  not a claim that it is there yet, and the page says so where it links. */
export const REPO_URL = 'https://github.com/aashish254/flash-coder'
export const VERSION = '0.1.0'

export const COMMANDS = {
  install: 'python3.11 -m venv .venv && .venv/bin/pip install -e .[dev]',
  doctor: '.venv/bin/python -m flash.cli doctor',
  battery: '.venv/bin/python benchmarks/battery_reread.py',
  session: '.venv/bin/python -m flash.cli session --context . --test t.py',
  measure: 'python benchmarks/dashboard_data.py && python benchmarks/export_site_data.py',
}
