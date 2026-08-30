package com.shinyoohag.deepfakeclassifier

import java.nio.FloatBuffer
import kotlin.math.floor

/**
 * Pure-Kotlin preprocessing for the exported ONNX detector.
 *
 * Matches `experiments/train_detector.py`'s transform: square resize to
 * `size` (bilinear), RGB channel order, ImageNet mean/std normalization,
 * NCHW float layout. Kept free of Android classes so it is unit-testable
 * on the JVM.
 */
object OnnxPreprocessing {
    const val INPUT_SIZE: Int = 224

    val MEAN = floatArrayOf(0.485f, 0.456f, 0.406f)
    val STD = floatArrayOf(0.229f, 0.224f, 0.225f)

    /**
     * Convert a packed-ARGB pixel array (as returned by
     * [android.graphics.Bitmap.getPixels]) into a normalized NCHW float
     * buffer for the ONNX model.
     */
    fun preprocessArgb(argb: IntArray, width: Int, height: Int, size: Int = INPUT_SIZE): FloatBuffer {
        require(width > 0 && height > 0) { "image dimensions must be positive" }
        require(argb.size == width * height) { "pixel array size ${argb.size} does not match ${width}x$height" }

        val buffer = FloatBuffer.allocate(3 * size * size)
        for (channel in 0 until 3) {
            val shift = 16 - 8 * channel // R=16, G=8, B=0
            for (dy in 0 until size) {
                val sy = (dy + 0.5f) * height / size - 0.5f
                val rowBase0 = floor(sy).toInt().coerceIn(0, height - 1) * width
                val rowBase1 = (floor(sy).toInt() + 1).coerceIn(0, height - 1) * width
                val wy = (sy - floor(sy)).coerceIn(0.0f, 1.0f)
                for (dx in 0 until size) {
                    val sx = (dx + 0.5f) * width / size - 0.5f
                    val x0 = floor(sx).toInt().coerceIn(0, width - 1)
                    val x1 = (floor(sx).toInt() + 1).coerceIn(0, width - 1)
                    val wx = (sx - floor(sx)).coerceIn(0.0f, 1.0f)

                    val p00 = argb[rowBase0 + x0]
                    val p01 = argb[rowBase0 + x1]
                    val p10 = argb[rowBase1 + x0]
                    val p11 = argb[rowBase1 + x1]

                    val v00 = ((p00 shr shift) and 0xFF) / 255f
                    val v01 = ((p01 shr shift) and 0xFF) / 255f
                    val v10 = ((p10 shr shift) and 0xFF) / 255f
                    val v11 = ((p11 shr shift) and 0xFF) / 255f

                    val top = v00 * (1f - wx) + v01 * wx
                    val bottom = v10 * (1f - wx) + v11 * wx
                    val value = top * (1f - wy) + bottom * wy
                    buffer.put((value - MEAN[channel]) / STD[channel])
                }
            }
        }
        buffer.rewind()
        return buffer
    }
}
