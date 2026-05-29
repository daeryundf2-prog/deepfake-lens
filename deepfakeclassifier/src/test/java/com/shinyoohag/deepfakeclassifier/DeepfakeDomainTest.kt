package com.shinyoohag.deepfakeclassifier

import java.io.ByteArrayOutputStream
import java.util.zip.DeflaterOutputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DeepfakeDomainTest {

    @Test
    fun textAnalysis_detectsAiDisclosureAndStructuredWriting() {
        val text = """
            As an AI language model, I can provide a balanced approach.
            1. It is important to note the context.
            2. Overall, this can help users understand the issue.
            3. In conclusion, the answer depends on multiple perspectives.
        """.trimIndent()

        val result = DeepfakeClassifier.analyzeText(text)

        assertTrue(result.score >= 35)
        assertTrue(result.signals.any { it.title.contains("AI") })
        assertEquals(RiskBand.HIGH, result.band)
        assertEquals("AI 어시스턴트 문체 추정", result.sourceGuess.label)
    }

    @Test
    fun textAnalysis_keepsPersonalShortTextLowRisk() {
        val text = "오늘은 학교에서 친구랑 점심을 먹고 3시에 집에 왔다. 사진 속 장소는 내가 자주 가는 카페다."

        val result = DeepfakeClassifier.analyzeText(text)

        assertEquals(RiskBand.LOW, result.band)
        assertTrue(result.score < 20)
        assertEquals(SourceConfidence.UNKNOWN, result.sourceGuess.confidence)
    }

    @Test
    fun textAnalysis_blankTextIsUnknown() {
        val result = DeepfakeClassifier.analyzeText("")

        assertEquals(RiskBand.UNKNOWN, result.band)
        assertEquals(0, result.score)
    }

    @Test
    fun textAnalysis_genericAiLikeTextDoesNotInventVendor() {
        val text = """
            결론적으로 이 문제는 다양한 관점에서 접근해야 합니다.
            첫째, 중요한 것은 맥락을 균형 잡힌 방식으로 이해하는 것입니다.
            둘째, 전반적으로 사용자의 목적과 상황을 고려하는 것이 도움이 됩니다.
        """.trimIndent()

        val result = DeepfakeClassifier.analyzeText(text)

        assertEquals(SourceConfidence.UNKNOWN, result.sourceGuess.confidence)
        assertEquals("출처 단서 없음", result.sourceGuess.label)
    }

    @Test
    fun imageAnalysis_generatedMetadataRaisesRiskAndSourceConfidence() {
        val result = DeepfakeClassifier.analyzeImage(
            ImageSample(
                width = 64,
                height = 64,
                pixels = solidPixels(64, 64, 0xff777777.toInt()),
                sourceWidth = 1024,
                sourceHeight = 1024,
                metadata = mapOf("Software" to "Stable Diffusion")
            )
        )

        assertEquals(RiskBand.HIGH, result.band)
        assertEquals("Stable Diffusion 추정", result.sourceGuess.label)
        assertEquals(SourceConfidence.HIGH, result.sourceGuess.confidence)
        assertTrue(result.signals.any { it.title.contains("메타데이터") })
    }

    @Test
    fun imageAnalysis_a1111ParametersGuessStableDiffusion() {
        val result = DeepfakeClassifier.analyzeImage(
            ImageSample(
                width = 64,
                height = 64,
                pixels = solidPixels(64, 64, 0xff777777.toInt()),
                sourceWidth = 768,
                sourceHeight = 768,
                metadata = mapOf(
                    "png.parameters" to """
                        portrait, cinematic lighting
                        Negative prompt: blurry
                        Steps: 30, Sampler: DPM++ 2M, CFG scale: 7, Seed: 1234, Model hash: abc123
                    """.trimIndent()
                )
            )
        )

        assertEquals(RiskBand.HIGH, result.band)
        assertEquals("Stable Diffusion / A1111 추정", result.sourceGuess.label)
        assertEquals(SourceConfidence.HIGH, result.sourceGuess.confidence)
    }

    @Test
    fun imageAnalysis_comfyWorkflowGuessComfyUi() {
        val result = DeepfakeClassifier.analyzeImage(
            ImageSample(
                width = 64,
                height = 64,
                pixels = solidPixels(64, 64, 0xff777777.toInt()),
                sourceWidth = 1024,
                sourceHeight = 1024,
                metadata = mapOf(
                    "png.workflow" to """{"1":{"class_type":"KSampler","inputs":{"seed":1}}}"""
                )
            )
        )

        assertEquals(RiskBand.HIGH, result.band)
        assertEquals("ComfyUI 추정", result.sourceGuess.label)
        assertEquals(SourceConfidence.HIGH, result.sourceGuess.confidence)
    }

    @Test
    fun imageAnalysis_midjourneyMetadataGuessMidjourney() {
        val result = DeepfakeClassifier.analyzeImage(
            ImageSample(
                width = 64,
                height = 64,
                pixels = solidPixels(64, 64, 0xff777777.toInt()),
                sourceWidth = 1344,
                sourceHeight = 768,
                metadata = mapOf("ImageDescription" to "Midjourney generated artwork")
            )
        )

        assertEquals("Midjourney/Niji 추정", result.sourceGuess.label)
        assertEquals(SourceConfidence.HIGH, result.sourceGuess.confidence)
        assertEquals(RiskBand.HIGH, result.band)
    }

    @Test
    fun imageAnalysis_squareResolutionAloneIsNotEnoughForHighRisk() {
        val result = DeepfakeClassifier.analyzeImage(
            ImageSample(
                width = 64,
                height = 64,
                pixels = solidPixels(64, 64, 0xff888888.toInt()),
                sourceWidth = 1024,
                sourceHeight = 1024
            )
        )

        assertEquals(RiskBand.LOW, result.band)
        assertTrue(result.score < 35)
    }

    @Test
    fun imageAnalysis_highFrequencyPatternAddsSignal() {
        val result = DeepfakeClassifier.analyzeImage(
            ImageSample(
                width = 64,
                height = 64,
                pixels = checkerPixels(64, 64),
                sourceWidth = 960,
                sourceHeight = 640
            )
        )

        assertTrue(result.signals.any { it.title.contains("고주파") })
    }

    @Test
    fun pngMetadataReader_extractsTextChunkParameters() {
        val png = pngWithChunks(
            chunk("tEXt", "parameters\u0000prompt\nNegative prompt: blur\nSteps: 20, Sampler: Euler, CFG scale: 7, Seed: 42")
        )

        val metadata = PngMetadataReader.read(png)

        assertTrue(metadata["png.parameters"]!!.contains("Negative prompt"))
    }

    @Test
    fun pngMetadataReader_extractsInternationalWorkflow() {
        val png = pngWithChunks(
            chunk("iTXt", "workflow\u0000\u0000\u0000\u0000\u0000{\"class_type\":\"KSampler\"}")
        )

        val metadata = PngMetadataReader.read(png)

        assertTrue(metadata["png.workflow"]!!.contains("KSampler"))
    }

    @Test
    fun pngMetadataReader_extractsCompressedPrompt() {
        val compressed = ByteArrayOutputStream()
        DeflaterOutputStream(compressed).use { output ->
            output.write("Steps: 10, Sampler: Euler, CFG scale: 5, Seed: 9".toByteArray())
        }
        val data = "parameters\u0000\u0000".toByteArray() + compressed.toByteArray()
        val png = pngWithChunks(chunk("zTXt", data))

        val metadata = PngMetadataReader.read(png)

        assertTrue(metadata["png.parameters"]!!.contains("Sampler"))
    }

    @Test
    fun pngMetadataReader_invalidBytesReturnEmptyMetadata() {
        assertTrue(PngMetadataReader.read(byteArrayOf(1, 2, 3)).isEmpty())
    }

    @Test
    fun batchScanSortsCandidatesBeforeLowAndUnsupported() {
        val high = BatchScanItem(
            name = "ai.png",
            kind = BatchItemKind.IMAGE,
            status = BatchItemStatus.ANALYZED,
            result = ClassificationResult(
                score = 80,
                band = RiskBand.HIGH,
                verdict = "사진 에서 의심 신호가 강합니다.",
                signals = emptyList(),
                limitations = emptyList()
            )
        )
        val low = BatchScanItem(
            name = "family.jpg",
            kind = BatchItemKind.IMAGE,
            status = BatchItemStatus.ANALYZED,
            result = ClassificationResult(
                score = 2,
                band = RiskBand.LOW,
                verdict = "사진 에서 뚜렷한 의심 신호는 적습니다.",
                signals = emptyList(),
                limitations = emptyList()
            )
        )
        val unsupported = BatchScanItem(
            name = "movie.mp4",
            kind = BatchItemKind.UNSUPPORTED,
            status = BatchItemStatus.UNSUPPORTED
        )

        val sorted = BatchScan.sort(listOf(unsupported, low, high))

        assertEquals(listOf("ai.png", "family.jpg", "movie.mp4"), sorted.map { it.name })
    }

    private fun solidPixels(width: Int, height: Int, color: Int): IntArray {
        return IntArray(width * height) { color }
    }

    private fun checkerPixels(width: Int, height: Int): IntArray {
        return IntArray(width * height) { index ->
            val x = index % width
            val y = index / width
            if ((x + y) % 2 == 0) 0xff101010.toInt() else 0xffeeeeee.toInt()
        }
    }

    private fun pngWithChunks(vararg chunks: ByteArray): ByteArray {
        val signature = byteArrayOf(
            0x89.toByte(),
            0x50,
            0x4e,
            0x47,
            0x0d,
            0x0a,
            0x1a,
            0x0a
        )
        return signature + chunks.reduce { acc, bytes -> acc + bytes } + chunk("IEND", ByteArray(0))
    }

    private fun chunk(type: String, text: String): ByteArray = chunk(type, text.toByteArray())

    private fun chunk(type: String, data: ByteArray): ByteArray {
        return intBytes(data.size) + type.toByteArray() + data + byteArrayOf(0, 0, 0, 0)
    }

    private fun intBytes(value: Int): ByteArray {
        return byteArrayOf(
            ((value ushr 24) and 0xff).toByte(),
            ((value ushr 16) and 0xff).toByte(),
            ((value ushr 8) and 0xff).toByte(),
            (value and 0xff).toByte()
        )
    }
}
