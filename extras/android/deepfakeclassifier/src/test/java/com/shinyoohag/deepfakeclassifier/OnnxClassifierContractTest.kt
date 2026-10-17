package com.shinyoohag.deepfakeclassifier

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM-side contract coverage for the ONNX path.
 *
 * What CAN be tested here:
 *  - the classifier's interface constants (asset name, input/output tensor
 *    names) which must stay in sync with `experiments/export_onnx.py`,
 *  - the `NeuralScore` value type,
 *  - preprocessing behavior not covered by [OnnxPreprocessingTest]
 *    (real bilinear interpolation, non-square inputs, alpha independence).
 *
 * What CANNOT be tested on a plain JVM: `OnnxClassifier.classify` takes an
 * `android.content.Context` — instantiating or stubbing one on the unit-test
 * android.jar throws `RuntimeException("Stub!")` — and the session needs the
 * onnxruntime native library, which only ships inside the app. The
 * graceful-null-when-model-absent path is therefore covered by the
 * instrumented skeleton in `src/androidTest/` (see
 * `docs/deepfake-classifier.md`, "Testing the ONNX path").
 */
class OnnxClassifierContractTest {
    private fun argb(r: Int, g: Int, b: Int, a: Int = 0xFF): Int =
        (a shl 24) or (r shl 16) or (g shl 8) or b

    private fun toArray(buffer: java.nio.FloatBuffer): FloatArray {
        val values = FloatArray(buffer.limit())
        buffer.get(values)
        return values
    }

    private fun norm(value255: Int, channel: Int): Float =
        (value255 / 255f - OnnxPreprocessing.MEAN[channel]) / OnnxPreprocessing.STD[channel]

    private fun normF(value255: Float, channel: Int): Float =
        (value255 / 255f - OnnxPreprocessing.MEAN[channel]) / OnnxPreprocessing.STD[channel]

    // ---- classifier interface contract ------------------------------------

    @Test
    fun `interface constants match the export contract`() {
        // Pinned to experiments/export_onnx.py + src/main/assets/README.md.
        assertEquals("deepfake-lens.onnx", OnnxClassifier.MODEL_ASSET_NAME)
        assertEquals("input", OnnxClassifier.INPUT_NAME)
        assertEquals("logits", OnnxClassifier.OUTPUT_NAME)
    }

    @Test
    fun `neural score carries the synthetic-class probability`() {
        assertEquals(0.42f, NeuralScore(0.42f).aiProbability, 1e-6f)
    }

    // ---- preprocessing gaps (bilinear interpolation) -----------------------

    @Test
    fun `2x2 red diagonal upsampled to 3x3 interpolates midpoints`() {
        // Source R plane: [[0, 1], [1, 0]] (in 0..1). With align_corners=false
        // sampling, corners replicate and edge centers land on 0.5.
        val pixels = intArrayOf(
            argb(0, 0, 0), argb(255, 0, 0),
            argb(255, 0, 0), argb(0, 0, 0),
        )
        val values = toArray(OnnxPreprocessing.preprocessArgb(pixels, 2, 2, size = 3))
        val expectedR = floatArrayOf(
            0f, 0.5f, 1f,
            0.5f, 0.5f, 0.5f,
            1f, 0.5f, 0f,
        )
        for (i in expectedR.indices) {
            assertEquals("R plane index $i", (expectedR[i] - 0.485f) / 0.229f, values[i], 1e-4f)
        }
        // G and B planes are all-zero in the source.
        val zeroG = (0f - 0.456f) / 0.224f
        assertEquals(zeroG, values[9 + 4], 1e-5f)
        assertEquals((0f - 0.406f) / 0.225f, values[18 + 4], 1e-5f)
    }

    @Test
    fun `4x4 input downsampled to 2x2 averages quadrants`() {
        // G channel value = 10 * (y * 4 + x), i.e. rows [0..30] / [40..70] /
        // [80..110] / [120..150]. align_corners=false sampling at 2x2 makes
        // each output pixel the mean of one 2x2 source block.
        val pixels = IntArray(16) { i -> argb(0, 10 * i, 0) }
        val values = toArray(OnnxPreprocessing.preprocessArgb(pixels, 4, 4, size = 2))
        // Quadrant means: (0+10+40+50)/4=25, (20+30+60+70)/4=45,
        // (80+90+120+130)/4=105, (100+110+140+150)/4=125.
        assertEquals(norm(25, 1), values[4 + 0], 1e-3f)
        assertEquals(norm(45, 1), values[4 + 1], 1e-3f)
        assertEquals(norm(105, 1), values[4 + 2], 1e-3f)
        assertEquals(norm(125, 1), values[4 + 3], 1e-3f)
        // R plane was all zero.
        assertEquals(norm(0, 0), values[0], 1e-5f)
    }

    @Test
    fun `non-square input rescales each axis independently`() {
        // 4x2 -> 4x4: x is identity (sx lands on each source column), y
        // interpolates between the two source rows. G rows:
        // row0 = [0, 50, 150, 200], row1 = [10, 20, 30, 40].
        val pixels = intArrayOf(
            argb(0, 0, 0), argb(0, 50, 0), argb(0, 150, 0), argb(0, 200, 0),
            argb(0, 10, 0), argb(0, 20, 0), argb(0, 30, 0), argb(0, 40, 0),
        )
        val values = toArray(OnnxPreprocessing.preprocessArgb(pixels, 4, 2, size = 4))
        val gPlane = values.copyOfRange(16, 32)
        // dy=0: sy=-0.25 -> clamps to row0. dy=1: wy=0.25 blend.
        // dy=2: wy=0.75 blend. dy=3: clamps to row1.
        val expected = floatArrayOf(
            0f, 50f, 150f, 200f,
            2.5f, 42.5f, 120f, 160f,
            7.5f, 27.5f, 60f, 80f,
            10f, 20f, 30f, 40f,
        )
        for (i in expected.indices) {
            assertEquals("G plane index $i", normF(expected[i], 1), gPlane[i], 1e-3f)
        }
    }

    @Test
    fun `alpha byte does not affect output`() {
        val size = 2
        val opaque = IntArray(size * size) { argb(10, 20, 30, a = 0xFF) }
        val transparent = IntArray(size * size) { argb(10, 20, 30, a = 0x00) }
        val a = toArray(OnnxPreprocessing.preprocessArgb(opaque, size, size, size))
        val b = toArray(OnnxPreprocessing.preprocessArgb(transparent, size, size, size))
        assertTrue(a.contentEquals(b))
    }

    @Test
    fun `zero dimensions are rejected`() {
        val thrown = runCatching {
            OnnxPreprocessing.preprocessArgb(IntArray(0), width = 0, height = 2)
        }.exceptionOrNull()
        assertTrue(thrown is IllegalArgumentException)
    }
}
