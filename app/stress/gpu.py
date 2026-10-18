"""GPU stress: a compute shader (WebGPU through wgpu: Vulkan / DirectX 12 / Metal, so any GPU on Windows or Linux,
x64 or ARM64) runs a long chain of multiply-adds on millions of numbers, again and again. Every pass writes the
same answer; it is compared with the first pass (taken before the GPU got hot): a different answer is an error.
Passes per second are the work rate.
"""

import hashlib
import threading
import time
from typing import Optional

ITEMS = 1 << 22                 # 4M floats (16 MB)
WORKGROUP = 256
DISPATCHES = 4                  # dispatches per pass; the answer is read back and checked once per pass
SHADER = """
@group(0) @binding(0) var<storage, read> src: array<f32>;
@group(0) @binding(1) var<storage, read_write> dst: array<f32>;
struct Params { loops: u32, count: u32 }
@group(0) @binding(2) var<uniform> params: Params;

@compute @workgroup_size(256)
fn main(@builtin(global_invocation_id) id: vec3<u32>) {
    let i = id.x + id.y * 65535u * 256u;
    if (i >= params.count) { return; }
    let x = src[i];
    var v = vec4<f32>(x, 1.0 - x, 0.5 * x + 0.25, 0.75 - 0.5 * x);
    var w = v.wzyx * 0.5;
    let m1 = vec4<f32>(0.999991, 0.999993, 0.999989, 0.999997);
    let a1 = vec4<f32>(0.0000071, 0.0000059, 0.0000083, 0.0000027);
    let m2 = vec4<f32>(0.999995, 0.999987, 0.999999, 0.999983);
    let a2 = vec4<f32>(0.0000043, 0.0000097, 0.0000011, 0.0000131);
    for (var k = 0u; k < params.loops; k = k + 1u) {
        v = fma(v, m1, a1);
        w = fma(w, m2, a2);
        v = fma(v, m2, a2);
        w = fma(w, m1, a1);
    }
    let s = v + w;
    dst[i] = s.x + s.y + s.z + s.w;
}
"""


def available() -> tuple:
    try:
        import wgpu
    except ImportError:
        return False, "wgpu is not installed"
    try:
        adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
    except Exception as e:
        return False, f"no GPU for compute ({e})"
    if adapter is None:
        return False, "no GPU for compute"
    info = adapter.info
    return True, f"{info.get('device', 'GPU')} ({info.get('backend_type', '?')})"


class GpuStress:
    """start() a thread that keeps the GPU busy, read() (passes so far, errors so far), stop()."""

    def __init__(self, target_pass_seconds: float = 0.25):
        self.target = target_pass_seconds
        self.passes = 0
        self.errors = 0
        self.device_name: Optional[str] = None
        self.failure: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._pass_started = 0.0                       # when the pass now running began, and how long the last took
        self._pass_took = 0.0

    def start(self, timeout: float = 60.0):
        self._thread = threading.Thread(target=self._run, name="pcbm-gpu-stress", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self.failure:
            raise RuntimeError(self.failure)
        return self

    def read(self) -> tuple:
        """Passes done, counting the running pass by how far it is (a pass takes about a quarter second: whole
        passes alone would make each second's work jump between 3, 4 and 5)."""
        passes, started, took = self.passes, self._pass_started, self._pass_took
        part = min(0.99, (time.monotonic() - started) / took) if took > 0 and started else 0.0
        return passes + part, self.errors

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, grace: float = 15.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(grace)

    def _run(self):
        try:
            import numpy as np
            import wgpu
            adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
            if adapter is None:
                raise RuntimeError("no GPU for compute")
            self.device_name = adapter.info.get("device")
            device = adapter.request_device_sync()
            values = np.random.default_rng(20261018).random(ITEMS, dtype=np.float32)
            usage = wgpu.BufferUsage
            src = device.create_buffer_with_data(data=values, usage=usage.STORAGE)
            params = device.create_buffer(size=8, usage=usage.UNIFORM | usage.COPY_DST)
            module = device.create_shader_module(code=SHADER)
            pipeline = device.create_compute_pipeline(layout="auto", compute={"module": module, "entry_point": "main"})
            # two slots: while the answer of one pass is read and checked, the next pass is already on the GPU
            # (with one, the GPU waits for us between passes; with the CPU stress running that was 2/3 of the time)
            slots = []
            for _ in range(2):
                dst = device.create_buffer(size=values.nbytes, usage=usage.STORAGE | usage.COPY_SRC)
                staging = device.create_buffer(size=values.nbytes, usage=usage.MAP_READ | usage.COPY_DST)
                group = device.create_bind_group(layout=pipeline.get_bind_group_layout(0), entries=[
                    {"binding": 0, "resource": {"buffer": src}}, {"binding": 1, "resource": {"buffer": dst}},
                    {"binding": 2, "resource": {"buffer": params}}])
                slots.append((dst, staging, group))
            groups = ITEMS // WORKGROUP

            def submit(slot: int, loops: int):
                dst, staging, group = slots[slot]
                device.queue.write_buffer(params, 0, np.array([loops, ITEMS], dtype=np.uint32).tobytes())
                enc = device.create_command_encoder()
                cp = enc.begin_compute_pass()
                cp.set_pipeline(pipeline)
                cp.set_bind_group(0, group)
                for _ in range(DISPATCHES):
                    cp.dispatch_workgroups(min(groups, 65535), max(1, -(-groups // 65535)), 1)
                cp.end()
                enc.copy_buffer_to_buffer(dst, 0, staging, 0, values.nbytes)
                device.queue.submit([enc.finish()])

            def answer(slot: int) -> bytes:
                staging = slots[slot][1]
                staging.map_sync(wgpu.MapMode.READ)          # waits for that pass only
                try:
                    return hashlib.sha256(staging.read_mapped(copy=False)).digest()
                finally:
                    staging.unmap()

            def one_pass(loops: int) -> bytes:
                submit(0, loops)
                return answer(0)

            loops = 64                                    # find a pass of about target seconds
            while True:
                t0 = time.monotonic()
                one_pass(loops)
                took = time.monotonic() - t0
                if took >= self.target / 2 or loops >= 1 << 20:
                    loops = max(1, int(loops * self.target / max(took, 1e-6)))
                    break
                loops *= 4
            expected = one_pass(loops)                    # the answer, before the GPU is hot
            self._pass_started = time.monotonic()
            self._ready.set()
            slot = 0
            submit(slot, loops)
            while not self._stop.is_set():
                submit(1 - slot, loops)                       # queued before we wait for this one
                if answer(slot) != expected:
                    self.errors += 1
                slot = 1 - slot
                now = time.monotonic()
                self._pass_took, self._pass_started = now - self._pass_started, now
                self.passes += 1
        except Exception as e:
            self.failure = f"GPU stress stopped: {e}"
        finally:
            self._ready.set()
