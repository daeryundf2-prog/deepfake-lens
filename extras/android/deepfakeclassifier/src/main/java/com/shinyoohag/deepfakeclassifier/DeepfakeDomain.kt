package com.shinyoohag.deepfakeclassifier

import java.util.Locale
import kotlin.math.abs
import kotlin.math.ln
import kotlin.math.pow
import kotlin.math.sqrt

enum class RiskBand(val label: String, val shortLabel: String) {
    UNKNOWN("판단 어려움", "판단 어려움"),
    LOW("낮은 가능성", "낮음"),
    MEDIUM("검토 필요", "주의"),
    HIGH("높은 가능성", "높음")
}

enum class SourceConfidence(val label: String) {
    UNKNOWN("알 수 없음"),
    LOW("낮음"),
    MEDIUM("중간"),
    HIGH("높음")
}

data class SourceGuess(
    val label: String,
    val confidence: SourceConfidence,
    val reasons: List<String> = emptyList()
) {
    companion object {
        fun unknown(reason: String = "출처를 판단할 메타데이터나 명시적 단서가 없습니다."): SourceGuess {
            return SourceGuess(
                label = "출처 단서 없음",
                confidence = SourceConfidence.UNKNOWN,
                reasons = listOf(reason)
            )
        }
    }
}

data class EvidenceSignal(
    val title: String,
    val detail: String,
    val weight: Int
)

data class ClassificationResult(
    val score: Int,
    val band: RiskBand,
    val verdict: String,
    val signals: List<EvidenceSignal>,
    val limitations: List<String>,
    val sourceGuess: SourceGuess = SourceGuess.unknown(),
    val nextChecks: List<String> = defaultNextChecks
) {
    fun displaySignals(limit: Int = 3): List<EvidenceSignal> = signals.take(limit)

    companion object {
        val defaultNextChecks = listOf(
            "원본 출처와 업로드 맥락을 확인하세요.",
            "가능하면 원본 파일의 메타데이터를 다시 확인하세요.",
            "사진은 역이미지 검색이나 원본 촬영본 비교를 함께 보세요."
        )
    }
}

data class ImageSample(
    val width: Int,
    val height: Int,
    val pixels: IntArray,
    val sourceWidth: Int = width,
    val sourceHeight: Int = height,
    val metadata: Map<String, String> = emptyMap()
)

enum class BatchItemKind(val label: String) {
    TEXT("글"),
    IMAGE("사진"),
    UNSUPPORTED("미지원")
}

enum class BatchItemStatus(val label: String) {
    ANALYZED("분석 완료"),
    UNSUPPORTED("지원 안 함"),
    FAILED("읽기 실패")
}

data class BatchScanItem(
    val name: String,
    val kind: BatchItemKind,
    val status: BatchItemStatus,
    val result: ClassificationResult? = null,
    val errorMessage: String? = null
)

data class BatchScanSummary(
    val total: Int,
    val candidates: Int,
    val needsReview: Int,
    val lowSignal: Int,
    val unknown: Int,
    val unsupportedOrFailed: Int
)

object BatchScan {
    fun sort(items: List<BatchScanItem>): List<BatchScanItem> {
        return items.sortedWith(
            compareBy<BatchScanItem> { item -> sortBucket(item) }
                .thenByDescending { it.result?.score ?: -1 }
                .thenBy { it.name.lowercase(Locale.ROOT) }
        )
    }

    fun summarize(items: List<BatchScanItem>): BatchScanSummary {
        return BatchScanSummary(
            total = items.size,
            candidates = items.count { it.result?.band == RiskBand.HIGH },
            needsReview = items.count { it.result?.band == RiskBand.MEDIUM },
            lowSignal = items.count { it.result?.band == RiskBand.LOW },
            unknown = items.count { it.result?.band == RiskBand.UNKNOWN },
            unsupportedOrFailed = items.count { it.status != BatchItemStatus.ANALYZED }
        )
    }

    private fun sortBucket(item: BatchScanItem): Int {
        if (item.status != BatchItemStatus.ANALYZED) return 4
        return when (item.result?.band) {
            RiskBand.HIGH -> 0
            RiskBand.MEDIUM -> 1
            RiskBand.UNKNOWN -> 2
            RiskBand.LOW -> 3
            null -> 4
        }
    }
}

object DeepfakeClassifier {
    private val aiIdentityPhrases = listOf(
        "as an ai",
        "language model",
        "i cannot browse",
        "ai assistant",
        "인공지능으로서",
        "언어 모델",
        "제가 직접 경험할 수는",
        "실시간으로 확인할 수는"
    )

    private val syntheticWritingPhrases = listOf(
        "결론적으로",
        "요약하자면",
        "다음과 같습니다",
        "중요한 것은",
        "다양한 관점",
        "균형 잡힌",
        "전반적으로",
        "필수적입니다",
        "도움이 됩니다",
        "it is important to note",
        "in conclusion",
        "overall",
        "from multiple perspectives",
        "balanced approach"
    )

    private val personalAnchors = listOf(
        "나",
        "저",
        "우리",
        "오늘",
        "어제",
        "내일",
        "엄마",
        "아빠",
        "친구",
        "학교",
        "회사",
        "집",
        "i",
        "me",
        "my",
        "we",
        "today",
        "yesterday",
        "tomorrow"
    )

    fun analyzeText(text: String): ClassificationResult {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) {
            return ClassificationResult(
                score = 0,
                band = RiskBand.UNKNOWN,
                verdict = "분석할 글이 없습니다.",
                signals = emptyList(),
                limitations = listOf("문장을 붙여 넣으면 문체, 반복, 구조화 패턴을 기준으로 신호를 계산합니다."),
                sourceGuess = SourceGuess.unknown(),
                nextChecks = listOf("분석할 원문을 더 길게 확보하세요.")
            )
        }

        val normalized = trimmed.lowercase(Locale.ROOT).replace(Regex("\\s+"), " ")
        val lines = trimmed.lines().map { it.trim() }.filter { it.isNotEmpty() }
        val sentences = splitSentences(trimmed)
        val words = Regex("[\\p{L}\\p{N}']+")
            .findAll(normalized)
            .map { it.value }
            .toList()
        val signals = mutableListOf<EvidenceSignal>()

        val identityHits = aiIdentityPhrases.count { normalized.contains(it) }
        if (identityHits > 0) {
            signals += EvidenceSignal(
                title = "AI 자기표현 문구",
                detail = "AI 또는 언어 모델임을 직접 암시하는 표현이 $identityHits 개 발견되었습니다.",
                weight = 35
            )
        }

        val phraseHits = syntheticWritingPhrases.count { normalized.contains(it) }
        when {
            phraseHits >= 4 -> signals += EvidenceSignal(
                title = "템플릿형 문장 전개",
                detail = "요약/균형/결론형 연결 문구가 반복되어 자동 생성 문체와 비슷합니다.",
                weight = 22
            )
            phraseHits >= 2 -> signals += EvidenceSignal(
                title = "정형화된 연결 문구",
                detail = "자동 생성 글에서 자주 보이는 연결 표현이 $phraseHits 개 보입니다.",
                weight = 12
            )
        }

        val listMarkers = lines.count { it.matches(Regex("""^(\d+[\).]|[-*•])\s+.+""")) }
        when {
            listMarkers >= 6 -> signals += EvidenceSignal(
                title = "과도하게 균일한 목록 구조",
                detail = "목록 항목이 $listMarkers 개 이어져 답변형 생성물일 가능성을 높입니다.",
                weight = 18
            )
            listMarkers >= 3 -> signals += EvidenceSignal(
                title = "목록 중심 구성",
                detail = "짧은 설명보다 번호/불릿 구조가 두드러집니다.",
                weight = 10
            )
        }

        sentenceUniformitySignal(sentences)?.let { signals += it }
        repeatedShingleSignal(words)?.let { signals += it }
        genericTextSignal(normalized, words)?.let { signals += it }

        val sourceGuess = GenerationMetadata.guessTextSource(normalized, identityHits)
        val limitations = buildList {
            add("이 MVP는 설명 가능한 휴리스틱 분류기라서 판정은 법적/최종 진위 판단이 아니라 검토 우선순위입니다.")
            if (trimmed.length < 240) {
                add("짧은 글은 문체 통계가 불안정하므로 긴 본문보다 오탐 가능성이 높습니다.")
            }
            if (sentences.size < 4) {
                add("문장 수가 적어 반복도와 문장 길이 균일성 신호가 제한적으로만 반영되었습니다.")
            }
        }

        val forceUnknown = trimmed.length < 24 && signals.isEmpty() && sourceGuess.confidence == SourceConfidence.UNKNOWN
        return buildResult(
            signals = signals,
            limitations = limitations,
            subject = "글",
            sourceGuess = sourceGuess,
            forceUnknown = forceUnknown
        )
    }

    fun analyzeImage(sample: ImageSample): ClassificationResult {
        val signals = mutableListOf<EvidenceSignal>()
        val metadataText = sample.metadata.entries
            .joinToString(" ") { "${it.key} ${it.value}" }
            .lowercase(Locale.ROOT)
        val sourceGuess = GenerationMetadata.guessImageSource(sample.metadata)

        if (sourceGuess.confidence.ordinal >= SourceConfidence.MEDIUM.ordinal) {
            signals += EvidenceSignal(
                title = "생성 도구 메타데이터",
                detail = sourceGuess.reasons.firstOrNull()
                    ?: "이미지 메타데이터에서 생성 도구 단서가 발견되었습니다.",
                weight = if (sourceGuess.confidence == SourceConfidence.HIGH) 67 else 36
            )
        }

        if (sample.sourceWidth == sample.sourceHeight && sample.sourceWidth >= 512 && sample.sourceWidth % 64 == 0) {
            signals += EvidenceSignal(
                title = "생성 모델에 흔한 정사각 해상도",
                detail = "${sample.sourceWidth}x${sample.sourceHeight} 해상도는 생성 이미지 워크플로에서 자주 쓰입니다.",
                weight = 9
            )
        }

        val metrics = computeImageMetrics(sample)
        if (metrics != null) {
            if (metrics.edgeDensity < 0.018 && metrics.lumaEntropy < 2.25) {
                signals += EvidenceSignal(
                    title = "과도하게 매끈한 픽셀 분포",
                    detail = "자연 사진보다 경계와 명암 변화가 매우 적습니다.",
                    weight = 10
                )
            }
            if (metrics.edgeDensity > 0.34 && metrics.lumaEntropy > 0.75) {
                signals += EvidenceSignal(
                    title = "고주파 질감/압축 흔적",
                    detail = "세부 질감 변화가 과하게 촘촘해 합성 또는 재압축 검토가 필요합니다.",
                    weight = 9
                )
            }
            if (metrics.averageSaturation > 0.42 && metrics.saturationStdDev < 0.17 && metrics.edgeDensity < 0.12) {
                signals += EvidenceSignal(
                    title = "균질한 색감",
                    detail = "채도는 높지만 영역 간 변화가 작아 렌더링된 이미지와 비슷한 패턴입니다.",
                    weight = 12
                )
            }
            if (metrics.gridSimilarity > 0.82 && sample.width >= 512 && sample.height >= 512) {
                signals += EvidenceSignal(
                    title = "반복적인 지역 패턴",
                    detail = "격자 영역 간 밝기/채도 패턴이 지나치게 유사합니다.",
                    weight = 11
                )
            }
        }

        val limitations = buildList {
            add("이미지 분석은 메타데이터와 픽셀 통계 기반 MVP입니다. 얼굴 위변조 판단에는 전용 학습 모델과 원본 비교가 필요합니다.")
            if (metadataText.isBlank()) {
                add("메타데이터가 없거나 앱에서 읽지 못해 생성 도구 흔적은 제한적으로만 확인했습니다.")
            }
            if (metrics == null) {
                add("픽셀 샘플을 만들지 못해 해상도와 메타데이터 위주로만 분석했습니다.")
            }
        }

        val forceUnknown = metrics == null && sourceGuess.confidence == SourceConfidence.UNKNOWN
        return buildResult(
            signals = signals,
            limitations = limitations,
            subject = "사진",
            sourceGuess = sourceGuess,
            forceUnknown = forceUnknown
        )
    }

    private fun buildResult(
        signals: List<EvidenceSignal>,
        limitations: List<String>,
        subject: String,
        sourceGuess: SourceGuess,
        forceUnknown: Boolean = false
    ): ClassificationResult {
        val sortedSignals = signals.sortedByDescending { it.weight }
        val score = sortedSignals.sumOf { it.weight }.coerceIn(0, 100)
        val band = when {
            forceUnknown -> RiskBand.UNKNOWN
            score >= 67 -> RiskBand.HIGH
            score >= 35 -> RiskBand.MEDIUM
            else -> RiskBand.LOW
        }
        val verdict = when (band) {
            RiskBand.UNKNOWN -> "$subject 에서 판단할 단서가 부족합니다."
            RiskBand.HIGH -> "$subject 에서 의심 신호가 강합니다."
            RiskBand.MEDIUM -> "$subject 에서 몇 가지 의심 신호가 보여 추가 확인이 필요합니다."
            RiskBand.LOW -> "$subject 에서 뚜렷한 의심 신호는 적습니다."
        }
        return ClassificationResult(
            score = score,
            band = band,
            verdict = verdict,
            signals = sortedSignals,
            limitations = limitations,
            sourceGuess = sourceGuess,
            nextChecks = nextChecksFor(subject)
        )
    }

    private fun nextChecksFor(subject: String): List<String> {
        return if (subject == "사진") {
            listOf(
                "원본 파일을 확보해 메타데이터가 남아 있는지 확인하세요.",
                "역이미지 검색이나 같은 장면의 원본 촬영본을 비교하세요.",
                "게시 계정의 반복 패턴과 업로드 맥락을 함께 보세요."
            )
        } else {
            listOf(
                "작성자가 직접 쓴 초안이나 편집 이력을 확인하세요.",
                "짧은 문단 하나보다 전체 글의 맥락을 함께 분석하세요.",
                "특정 AI 도구명이 직접 언급되었는지 원문을 다시 확인하세요."
            )
        }
    }

    private fun splitSentences(text: String): List<String> {
        return text.split(Regex("[.!?。！？\\n]+"))
            .map { it.trim() }
            .filter { it.length >= 8 }
    }

    private fun sentenceUniformitySignal(sentences: List<String>): EvidenceSignal? {
        if (sentences.size < 5) return null
        val lengths = sentences.map { sentence ->
            Regex("[\\p{L}\\p{N}']+").findAll(sentence).count().coerceAtLeast(1)
        }
        val average = lengths.average()
        val variance = lengths.sumOf { (it - average).pow(2) } / lengths.size
        val coefficient = sqrt(variance) / average.coerceAtLeast(1.0)
        return when {
            average >= 18.0 && coefficient < 0.28 -> EvidenceSignal(
                title = "문장 길이 균일성",
                detail = "여러 문장이 비슷한 길이로 이어져 자동 생성 문체와 유사합니다.",
                weight = 15
            )
            average >= 14.0 && coefficient < 0.38 -> EvidenceSignal(
                title = "낮은 문장 변주",
                detail = "문장 길이 변화가 작아 사람이 쓴 산문보다 리듬이 균일합니다.",
                weight = 9
            )
            else -> null
        }
    }

    private fun repeatedShingleSignal(words: List<String>): EvidenceSignal? {
        if (words.size < 80) return null
        val shingles = words.windowed(size = 3, step = 1) { it.joinToString(" ") }
        if (shingles.isEmpty()) return null
        val repeated = shingles.size - shingles.toSet().size
        val ratio = repeated.toDouble() / shingles.size
        return when {
            ratio >= 0.1 -> EvidenceSignal(
                title = "반복 어구",
                detail = "3단어 구문 반복률이 ${(ratio * 100).toInt()}% 입니다.",
                weight = 16
            )
            ratio >= 0.055 -> EvidenceSignal(
                title = "약한 반복 패턴",
                detail = "비슷한 구문이 여러 번 재사용됩니다.",
                weight = 8
            )
            else -> null
        }
    }

    private fun genericTextSignal(normalized: String, words: List<String>): EvidenceSignal? {
        if (words.size < 70) return null
        val hasNumberOrDate = Regex("""\d{1,4}([./:-]\d{1,2})?""").containsMatchIn(normalized)
        val personalAnchorCount = personalAnchors.count {
            Regex("""(^|\s)${Regex.escape(it)}($|\s)""").containsMatchIn(normalized)
        }
        return if (!hasNumberOrDate && personalAnchorCount == 0) {
            EvidenceSignal(
                title = "개인 맥락 부족",
                detail = "긴 글인데 날짜, 수치, 구체적 경험 단서가 거의 없습니다.",
                weight = 8
            )
        } else {
            null
        }
    }

    private data class ImageMetrics(
        val edgeDensity: Double,
        val lumaEntropy: Double,
        val averageSaturation: Double,
        val saturationStdDev: Double,
        val gridSimilarity: Double
    )

    private fun computeImageMetrics(sample: ImageSample): ImageMetrics? {
        if (sample.width <= 1 || sample.height <= 1 || sample.pixels.isEmpty()) return null
        val expected = sample.width * sample.height
        if (sample.pixels.size < expected) return null

        val luma = DoubleArray(expected)
        val saturation = DoubleArray(expected)
        val histogram = IntArray(16)
        var satSum = 0.0

        for (index in 0 until expected) {
            val pixel = sample.pixels[index]
            val r = channel(pixel, 16)
            val g = channel(pixel, 8)
            val b = channel(pixel, 0)
            val y = (0.2126 * r) + (0.7152 * g) + (0.0722 * b)
            val sat = saturationOf(r, g, b)
            luma[index] = y
            saturation[index] = sat
            satSum += sat
            histogram[(y / 16.0).toInt().coerceIn(0, 15)] += 1
        }

        var edgeCount = 0
        var comparisons = 0
        for (y in 0 until sample.height - 1) {
            for (x in 0 until sample.width - 1) {
                val index = y * sample.width + x
                val right = index + 1
                val down = index + sample.width
                if (abs(luma[index] - luma[right]) > 28.0) edgeCount += 1
                if (abs(luma[index] - luma[down]) > 28.0) edgeCount += 1
                comparisons += 2
            }
        }
        val edgeDensity = edgeCount.toDouble() / comparisons.coerceAtLeast(1)
        val entropy = entropy(histogram, expected)
        val avgSat = satSum / expected
        val satVariance = saturation.sumOf { (it - avgSat).pow(2) } / expected
        val gridSimilarity = gridSimilarity(sample, luma, saturation)

        return ImageMetrics(
            edgeDensity = edgeDensity,
            lumaEntropy = entropy,
            averageSaturation = avgSat,
            saturationStdDev = sqrt(satVariance),
            gridSimilarity = gridSimilarity
        )
    }

    private fun channel(pixel: Int, shift: Int): Int = (pixel shr shift) and 0xff

    private fun saturationOf(r: Int, g: Int, b: Int): Double {
        val max = maxOf(r, g, b).toDouble()
        val min = minOf(r, g, b).toDouble()
        return if (max <= 0.0) 0.0 else (max - min) / max
    }

    private fun entropy(histogram: IntArray, total: Int): Double {
        if (total <= 0) return 0.0
        return histogram.sumOf { count ->
            if (count == 0) {
                0.0
            } else {
                val probability = count.toDouble() / total
                -probability * (ln(probability) / ln(2.0))
            }
        }
    }

    private fun gridSimilarity(sample: ImageSample, luma: DoubleArray, saturation: DoubleArray): Double {
        val grid = 6
        val cells = mutableListOf<Pair<Double, Double>>()
        for (row in 0 until grid) {
            for (col in 0 until grid) {
                val x0 = col * sample.width / grid
                val x1 = (col + 1) * sample.width / grid
                val y0 = row * sample.height / grid
                val y1 = (row + 1) * sample.height / grid
                var lumaSum = 0.0
                var satSum = 0.0
                var count = 0
                for (y in y0 until y1) {
                    for (x in x0 until x1) {
                        val index = y * sample.width + x
                        lumaSum += luma[index]
                        satSum += saturation[index]
                        count += 1
                    }
                }
                if (count > 0) {
                    cells += (lumaSum / count) to (satSum / count)
                }
            }
        }
        if (cells.size < 2) return 0.0
        var similar = 0
        var comparisons = 0
        for (index in 0 until cells.lastIndex) {
            val current = cells[index]
            val next = cells[index + 1]
            val lumaDistance = abs(current.first - next.first)
            val satDistance = abs(current.second - next.second)
            if (lumaDistance < 10.0 && satDistance < 0.05) similar += 1
            comparisons += 1
        }
        return similar.toDouble() / comparisons.coerceAtLeast(1)
    }
}
