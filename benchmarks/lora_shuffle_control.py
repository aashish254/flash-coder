"""R-6.4's control: the same weights, shuffled — so a before/after difference
can only come from what the adapter LEARNED, not from it existing.

An adapter that beats the base model proves two things at once unless the second
is held constant: that LoRA's extra parameters help (they are ~11.5M numbers
touched, a regulariser and a perturbation as much as a memory), and that the
*content* of the verified outcomes helped. This makes the first uninteresting:
every trainable tensor keeps its shape, its parameter count, its mean, its
variance and its exact multiset of values, and loses only the arrangement that
maps a weight to the neuron it was trained for.

So the three arms read as:

  base            the small tier as shipped
  after           base + the adapter fit on the agent's verified outcomes
  control         base + the same weights with their positions permuted

`after` beating `control` is the claim. `after` beating `base` while the control
also does is a LoRA-shaped artifact, not learning.

    python benchmarks/lora_shuffle_control.py [adapter-dir] [out-dir]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(src: str, dst: str) -> int:
    import mlx.core as mx

    from flash import loop

    s, d = Path(src), Path(dst)
    weights = s / "adapters.safetensors"
    if not weights.exists():
        print(f"no adapter at {s} — nothing to shuffle")
        return 1
    arrays = mx.load(str(weights))
    keys = sorted(arrays)
    out = {}
    mx.random.seed(20260926)
    for k in keys:
        a = arrays[k]
        flat = a.reshape(-1)
        # a permutation of the values, not of the tensor: the histogram is
        # exactly preserved, so the only difference is which weight sits where
        out[k] = flat[mx.random.permutation(flat.size)].reshape(a.shape)
    d.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(d / "adapters.safetensors"), out)
    (d / "adapter_config.json").write_text((s / "adapter_config.json").read_text())

    same_shape = all(out[k].shape == arrays[k].shape for k in keys)
    same_hist = all(mx.array_equal(mx.sort(out[k].reshape(-1)),
                                   mx.sort(arrays[k].reshape(-1))) for k in keys)
    moved = any(not mx.array_equal(out[k], arrays[k]) for k in keys)
    n = sum(int(arrays[k].size) for k in keys)
    # the loader must accept it under the same seam as the real one
    resolved = loop.adapter_path(str(d))
    info = json.loads((Path(resolved) / "adapter_config.json").read_text())
    print(f"{len(keys)} tensors, {n:,} trainable parameters")
    print(f"  shapes identical     {same_shape}")
    print(f"  value multisets kept {same_hist}")
    print(f"  arrangement changed  {moved}")
    print(f"  loads as an adapter  {resolved is not None} "
          f"(rank {info['lora_parameters']['rank']}, "
          f"scale {info['lora_parameters']['scale']}, "
          f"{info['num_layers']} layers trained)")
    ok = same_shape and same_hist and moved and resolved is not None
    print(f"-> control written to {d}" if ok else "-> CONTROL INCOMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    raise SystemExit(main(a[0] if a else str(ROOT / "benchmarks/results/adapters/v1"),
                          a[1] if len(a) > 1 else
                          str(ROOT / "benchmarks/results/adapters/v1-shuffled")))
