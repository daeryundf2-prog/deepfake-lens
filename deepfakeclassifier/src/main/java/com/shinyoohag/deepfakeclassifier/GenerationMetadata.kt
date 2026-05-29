package com.shinyoohag.deepfakeclassifier

import java.util.Locale

object GenerationMetadata {
    fun guessImageSource(metadata: Map<String, String>): SourceGuess {
        val blob = metadata.entries.joinToString("\n") { "${it.key}: ${it.value}" }
        val normalized = blob.lowercase(Locale.ROOT)
        if (normalized.isBlank()) return SourceGuess.unknown()

        if (looksLikeComfyUi(normalized)) {
            return SourceGuess(
                label = "ComfyUI 추정",
                confidence = SourceConfidence.HIGH,
                reasons = listOf("메타데이터에서 ComfyUI workflow/prompt 구조가 발견되었습니다.")
            )
        }

        if (looksLikeA1111(normalized)) {
            return SourceGuess(
                label = "Stable Diffusion / A1111 추정",
                confidence = SourceConfidence.HIGH,
                reasons = listOf("프롬프트, steps, sampler, CFG, seed 같은 A1111 생성 파라미터가 발견되었습니다.")
            )
        }

        directToolGuess(normalized)?.let { return it }

        if (containsGenerationFields(normalized)) {
            return SourceGuess(
                label = "AI 생성 메타데이터 추정",
                confidence = SourceConfidence.MEDIUM,
                reasons = listOf("생성 파라미터로 보이는 prompt/model/seed/CFG 계열 필드가 발견되었습니다.")
            )
        }

        return SourceGuess.unknown()
    }

    fun guessTextSource(normalizedText: String, aiIdentityHits: Int): SourceGuess {
        return when {
            normalizedText.contains("chatgpt") || normalizedText.contains("openai") -> SourceGuess(
                label = "ChatGPT/OpenAI 단서 있음",
                confidence = SourceConfidence.MEDIUM,
                reasons = listOf("원문에 ChatGPT 또는 OpenAI가 직접 언급되었습니다.")
            )
            normalizedText.contains("claude") || normalizedText.contains("anthropic") -> SourceGuess(
                label = "Claude 단서 있음",
                confidence = SourceConfidence.MEDIUM,
                reasons = listOf("원문에 Claude 또는 Anthropic이 직접 언급되었습니다.")
            )
            normalizedText.contains("gemini") || normalizedText.contains("bard") -> SourceGuess(
                label = "Gemini 단서 있음",
                confidence = SourceConfidence.MEDIUM,
                reasons = listOf("원문에 Gemini 또는 Bard가 직접 언급되었습니다.")
            )
            aiIdentityHits > 0 -> SourceGuess(
                label = "AI 어시스턴트 문체 추정",
                confidence = SourceConfidence.MEDIUM,
                reasons = listOf("AI 또는 언어 모델임을 직접 암시하는 문구가 있습니다.")
            )
            else -> SourceGuess.unknown()
        }
    }

    private fun directToolGuess(normalized: String): SourceGuess? {
        val rules = listOf(
            ToolRule("Midjourney/Niji 추정", listOf("midjourney", "niji"), "Midjourney/Niji 단서가 메타데이터에 있습니다."),
            ToolRule("Stable Diffusion 추정", listOf("stable diffusion", "stablediffusion", "automatic1111", "a1111", "sd-webui"), "Stable Diffusion 계열 단서가 메타데이터에 있습니다."),
            ToolRule("DALL-E/OpenAI 추정", listOf("dall-e", "dalle", "openai", "chatgpt"), "DALL-E/OpenAI 단서가 메타데이터에 있습니다."),
            ToolRule("Adobe Firefly 추정", listOf("adobe firefly", "firefly"), "Adobe Firefly 단서가 메타데이터에 있습니다."),
            ToolRule("Runway 추정", listOf("runway"), "Runway 단서가 메타데이터에 있습니다."),
            ToolRule("Leonardo.ai 추정", listOf("leonardo.ai", "leonardo ai"), "Leonardo.ai 단서가 메타데이터에 있습니다."),
            ToolRule("NovelAI 추정", listOf("novelai", "novel ai"), "NovelAI 단서가 메타데이터에 있습니다.")
        )
        val rule = rules.firstOrNull { candidate -> candidate.markers.any { normalized.contains(it) } }
        return rule?.let {
            SourceGuess(
                label = it.label,
                confidence = SourceConfidence.HIGH,
                reasons = listOf(it.reason)
            )
        }
    }

    private fun looksLikeA1111(normalized: String): Boolean {
        val hasPromptBlock = normalized.contains("negative prompt") || normalized.contains("png.parameters")
        val parameterHits = listOf(
            "steps:",
            "sampler:",
            "cfg scale",
            "seed:",
            "model hash",
            "model:"
        ).count { normalized.contains(it) }
        return hasPromptBlock && parameterHits >= 2
    }

    private fun looksLikeComfyUi(normalized: String): Boolean {
        val hasWorkflowKey = normalized.contains("png.workflow") ||
            normalized.contains("png.prompt") ||
            normalized.contains("\"workflow\"") ||
            normalized.contains("comfyui")
        val graphHits = listOf(
            "ksampler",
            "checkpointloadersimple",
            "loraloader",
            "\"class_type\"",
            "\"inputs\"",
            "\"widgets_values\""
        ).count { normalized.contains(it) }
        return hasWorkflowKey && graphHits >= 1
    }

    private fun containsGenerationFields(normalized: String): Boolean {
        val fields = listOf(
            "prompt",
            "negative prompt",
            "seed",
            "cfg",
            "sampler",
            "model hash",
            "model_name",
            "lora",
            "checkpoint"
        )
        return fields.count { normalized.contains(it) } >= 2
    }

    private data class ToolRule(
        val label: String,
        val markers: List<String>,
        val reason: String
    )
}
