package com.shinyoohag.deepfakeclassifier

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.FloatBuffer

private fun toArray(buffer: FloatBuffer): FloatArray {
    val values = FloatArray(buffer.limit())
    buffer.get(values)
    return values
}

class OnnxPreprocessingTest {
    private fun argb(r: Int, g: Int, b: Int): Int = (0xFF shl 24) or (r shl 16) or (g shl 8) or b

    @Test
    fun `white image normalizes to imagenet-standard values`() {
        val size = 4
        val pixels = IntArray(size * size) { argb(255, 255, 255) }
        val values = toArray(OnnxPreprocessing.preprocessArgb(pixels, size, size))
        assertEquals(3 * size * size, values.size)
        assertEquals((1f - 0.485f) / 0.229f, values[0], 1e-5f)
        assertEquals((1f - 0.456f) / 0.224f, values[size * size], 1e-5f)
        assertEquals((1f - 0.406f) / 0.225f, values[2 * size * size], 1e-5f)
    }

    @Test
    fun `black image normalizes to negative mean over std`() {
        val size = 2
        val pixels = IntArray(size * size) { argb(0, 0, 0) }
        val values = toArray(OnnxPreprocessing.preprocessArgb(pixels, size, size))
        assertEquals((0f - 0.485f) / 0.229f, values[0], 1e-5f)
    }

    @Test
    fun `channel planes are laid out NCHW`() {
        // Only the first pixel carries red; the planes must not bleed.
        val size = 2
        val pixels = IntArray(size * size) { argb(0, 0, 0) }.also { it[0] = argb(255, 0, 0) }
        val values = toArray(OnnxPreprocessing.preprocessArgb(pixels, size, size))
        assertEquals((1f - 0.485f) / 0.229f, values[0], 1e-5f)              // R plane, pixel (0,0)
        assertEquals((0f - 0.485f) / 0.229f, values[1], 1e-5f)              // R plane, pixel (1,0) is black
        assertEquals((0f - 0.456f) / 0.224f, values[size * size], 1e-5f)    // G plane, pixel (0,0) is black
        assertEquals((0f - 0.225f) / 0.225f, values[2 * size * size], 1e-5f) // B plane, pixel (0,0) is black
    }

    @Test
    fun `identity scale keeps pixel values`() {
        // A 2x2 image at size=2 must reproduce the exact source values.
        val size = 2
        val pixels = intArrayOf(
            argb(10, 20, 30), argb(40, 50, 60),
            argb(70, 80, 90), argb(100, 110, 120)
        )
        val values = toArray(OnnxPreprocessing.preprocessArgb(pixels, size, size))
        fun expected(channelValue: Int, channel: Int) =
            (channelValue / 255f - OnnxPreprocessing.MEAN[channel]) / OnnxPreprocessing.STD[channel]
        // NCHW: plane stride = size*size, within a plane row-major over (dy, dx).
        assertEquals(expected(10, 0), values[0], 1e-4f)                  // R (0,0)
        assertEquals(expected(40, 0), values[1], 1e-4f)                  // R (1,0)
        assertEquals(expected(70, 0), values[2], 1e-4f)                  // R (0,1)
        assertEquals(expected(20, 1), values[size * size], 1e-4f)        // G (0,0)
        assertEquals(expected(110, 1), values[size * size + 3], 1e-4f)   // G (1,1)
        assertEquals(expected(120, 2), values[2 * size * size + 3], 1e-4f) // B (1,1)
    }

    @Test
    fun `buffer is rewound for tensor consumption`() {
        val size = 2
        val pixels = IntArray(size * size)
        val buffer: FloatBuffer = OnnxPreprocessing.preprocessArgb(pixels, size, size)
        assertEquals(0, buffer.position())
        assertEquals(3 * size * size, buffer.limit().toLong())
    }

    @Test
    fun `mismatched pixel array is rejected`() {
        val thrown = runCatching {
            OnnxPreprocessing.preprocessArgb(IntArray(3), width = 2, height = 2)
        }.exceptionOrNull()
        assertTrue(thrown is IllegalArgumentException)
    }
}
