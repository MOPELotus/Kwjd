"""Pure CPU candidate for exact native BGR face fusion; not production enabled.

Only the blend expression is restricted to exact nonzero mask support. Parsing,
blur, full-frame warp, face order and the final facexlib dtype policy stay intact.
Pass ``convert_output=False`` to inspect the original floating intermediate.

The reference blend/dtype policy derives from facexlib0.3.0
utils/face_restoration_helper.py, MIT License:
Copyright (c) 2020 Xintao Wang

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import numpy as np


def _native_finite_case(background, faces):
    if background.dtype != np.uint8 or background.shape != (1080, 1920, 3):
        return False
    # Also keep adversarial finite inputs from overflowing a float32 recurrence.
    # These are dispatch conditions, never clipping or changing any mask value.
    if len(faces) > 16:
        return False
    for mask, face in faces:
        if (not isinstance(mask, np.ndarray) or mask.dtype != np.float32
                or mask.shape != (1080, 1920, 1)
                or not isinstance(face, np.ndarray) or face.dtype != np.uint8
                or face.shape != background.shape or not np.isfinite(mask).all()
                or np.min(mask) < -2 or np.max(mask) > 2):
            return False
    return True


def blend_faces(background, faces, *, convert_output=True):
    """Return an independent image from ordered ``(soft_mask, warped_face)`` pairs.

    Native fast path: uint8 H1080/W1920/BGR3 background and warped faces, float32
    H1080/W1920/1 masks. The active processor already uses float32 parse masks.
    Unsupported but broadcast-compatible arrays and nonfinite inputs evaluate
    the original full-frame NumPy expression, preserving NaN propagation.
    Invalid broadcast inputs fail with NumPy's error; no silent approximation.
    Caller must keep input arrays stable until the function returns.
    """
    if not isinstance(background, np.ndarray):
        raise TypeError('Background must be a NumPy array')
    faces = list(faces)
    if not faces:
        result = background.copy()
    elif _native_finite_case(background, faces):
        result = background.astype(np.float32, copy=True)
        for mask, face in faces:
            flat = mask[:, :, 0]
            rows = np.flatnonzero(np.any(flat, axis=1))
            if rows.size == 0:
                continue
            columns = np.flatnonzero(np.any(flat, axis=0))
            ys = slice(int(rows[0]), int(rows[-1]) + 1)
            xs = slice(int(columns[0]), int(columns[-1]) + 1)
            m = mask[ys, xs]
            # Preserve separate ufuncs, dtype, face order and original rounding.
            result[ys, xs] = m * face[ys, xs] + (1 - m) * result[ys, xs]
    else:
        result = background.copy()
        for mask, face in faces:
            result = mask * face + (1 - mask) * result
    if convert_output:
        # Retain facexlib's single final conversion, including its uint16 branch.
        return result.astype(np.uint16 if np.max(result) > 256 else np.uint8)
    return result
