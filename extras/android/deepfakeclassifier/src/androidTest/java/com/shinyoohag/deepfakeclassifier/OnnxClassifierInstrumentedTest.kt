package com.shinyoohag.deepfakeclassifier

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeFalse
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented contract tests for [OnnxClassifier].
 *
 * `classify` needs a real `android.content.Context`/`AssetManager` plus the
 * onnxruntime native library, so none of this can run on the plain JVM unit
 * tests (the stub android.jar throws `RuntimeException("Stub!")` on any
 * Context use, and no mocking framework is on the test classpath by design).
 *
 * Run on a device/emulator with:
 *   ./gradlew :deepfakeclassifier:connectedDebugAndroidTest
 *
 * No model file is committed. With the asset absent these tests pin the
 * documented degrade-to-heuristics behavior; if a developer drops
 * `deepfake-lens.onnx` into `src/main/assets/` (or androidTest assets), the
 * assumeTrue-gated test turns into a real end-to-end inference check.
 */
@RunWith(AndroidJUnit4::class)
class OnnxClassifierInstrumentedTest {

    private val context: Context
        get() = InstrumentationRegistry.getInstrumentation().targetContext

    private fun testBitmap(width: Int = 8, height: Int = 8): IntArray =
        IntArray(width * height) { i ->
            val r = (i * 37) and 0xFF
            val g = (i * 17) and 0xFF
            val b = (i * 91) and 0xFF
            (0xFF shl 24) or (r shl 16) or (g shl 8) or b
        }

    @Test
    fun modelAbsent_isModelAvailableReturnsFalse() {
        assumeFalse("model asset bundled — this check only applies without it", OnnxClassifier.isModelAvailable(context))
        assertFalse(OnnxClassifier.isModelAvailable(context))
    }

    @Test
    fun modelAbsent_classifyReturnsNullNotException() {
        assumeFalse("model asset bundled — this check only applies without it", OnnxClassifier.isModelAvailable(context))
        // Graceful-null contract: the caller must treat null as "heuristics
        // only", never as a verdict (see OnnxClassifier KDoc).
        assertNull(OnnxClassifier.classify(context, testBitmap(), 8, 8))
    }

    @Test
    fun modelPresent_classifyReturnsProbabilityInUnitRange() {
        // Self-activating E2E: only runs when a model asset exists. Do NOT
        // commit a model file — see src/main/assets/README.md for how to
        // export one locally.
        assumeTrue("no bundled deepfake-lens.onnx — skipping inference E2E", OnnxClassifier.isModelAvailable(context))
        val score = OnnxClassifier.classify(context, testBitmap(), 8, 8)
        assertNotNull("classify returned null despite a bundled model", score)
        val p = score!!.aiProbability
        assertTrue("aiProbability out of range: $p", p in 0f..1f)
    }
}
