# Archived TensorRT conversion and build recipes

These files are byte-for-byte copies of the locally executed recipes. They are
provenance evidence, not the production backend or an instruction to rebuild an
engine during installation. `prepare_trt_backend.py` only checks existing assets
and publishes an inactive candidate.

| File | Original location | SHA-256 |
| --- | --- | --- |
| `prepare_rife_neural_fp16.py` | `~/qingqingwu/post/bench/prepare_rife_neural_fp16.py` | `ed9c8e6ef07a2845230a2b4b3cd5ba29ac86a1240544653afacbd1cbdc386440` |
| `benchmark_rife_tensorrt.py` | `~/qingqingwu/post/bench/benchmark_rife_tensorrt.py` | `bc596e572c50540b3ed04da3eb26f4a53eaefbdf99e29aa0eb33905311b5d237` |

Do not execute the archived files in this directory. The conversion recipe uses
its own path to find `post/tools` and `post/reports`; the benchmark also reads
`rife_pair_01.png` and `rife_pair_02.png` next to its script. Reproduction requires
the unchanged recipes in the original `post/bench/` layout, the existing isolated
`post/cuda-venv`, all pinned source assets, and the actual native footage pair.
Never overwrite the original evidence or existing models, engine, or reports.
An independent experiment must preserve that relative layout in a separate
workspace and produce separate evidence; it does not inherit production approval.

The conversion recipe pins the official vs-mlrt RIFE v4.6 ONNX source hash and
ONNX version. The build recipe explicitly requires physical Tesla T10 visibility
through `CUDA_VISIBLE_DEVICES=1` and the pinned TensorRT version. Building or
benchmarking consumes GPU resources and requires separate scheduling; merely
archiving or preparing a candidate does neither. Engine, model, conversion,
benchmark, and recipe hashes remain bound by the local provenance JSON and
`cuda_trt.validate_assets`.
