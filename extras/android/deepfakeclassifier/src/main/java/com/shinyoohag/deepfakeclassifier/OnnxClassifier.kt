package com.shinyoohag.deepfakeclassifier

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context

/** Neural classification result: probability that the image is synthetic. */
data class NeuralScore(val aiProbability: Float)

/**
 * ONNX inference over the checkpoint produced by `experiments/train_detector.py`
 * + `experiments/export_onnx.py` (input "input" [1,3,224,224] float NCHW,
 * output "logits" [1,2] with classes real=0, synthetic-fake=1).
 *
 * The model file is not bundled: drop `deepfake-lens.onnx` into
 * `deepfakeclassifier/src/main/assets/`. Without it every classify call
 * returns null and the app falls back to heuristics only.
 */
object OnnxClassifier {
    const val MODEL_ASSET_NAME: String = "deepfake-lens.onnx"
    const val INPUT_NAME: String = "input"
    const val OUTPUT_NAME: String = "logits"

    @Volatile
    private var cachedSession: OrtSession? = null

    fun isModelAvailable(context: Context): Boolean {
        return runCatching {
            context.assets.open(MODEL_ASSET_NAME).use { true }
        }.getOrDefault(false)
    }

    /**
     * Run the detector and return the softmax probability of the synthetic
     * class. Returns null when no model is bundled or inference fails for
     * any reason — the caller must treat null as "heuristics only", never
     * as a verdict.
     */
    fun classify(context: Context, argb: IntArray, width: Int, height: Int): NeuralScore? {
        if (!isModelAvailable(context)) return null
        return runCatching {
            val session = obtainSession(context) ?: return@runCatching null
            val shape = longArrayOf(1, 3, OnnxPreprocessing.INPUT_SIZE.toLong(), OnnxPreprocessing.INPUT_SIZE.toLong())
            OnnxTensor.createTensor(OrtEnvironment.getEnvironment(), OnnxPreprocessing.preprocessArgb(argb, width, height), shape).use { tensor ->
                session.run(mapOf(INPUT_NAME to tensor)).use { output ->
                    val logits = (output[0].value as Array<FloatArray>)[0]
                    val maxLogit = logits.max()
                    val exponentials = logits.map { kotlin.math.exp((it - maxLogit).toDouble()) }
                    val total = exponentials.sum()
                    NeuralScore(aiProbability = (exponentials[1] / total).toFloat())
                }
            }
        }.getOrNull()
    }

    private fun obtainSession(context: Context): OrtSession? {
        cachedSession?.let { return it }
        return synchronized(this) {
            cachedSession ?: runCatching {
                val bytes = context.assets.open(MODEL_ASSET_NAME).use { it.readBytes() }
                OrtEnvironment.getEnvironment().createSession(bytes).also { cachedSession = it }
            }.getOrNull()
        }
    }
}
