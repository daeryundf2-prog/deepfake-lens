package com.shinyoohag.deepfakeclassifier

import android.content.ContentResolver
import android.content.Context
import android.database.Cursor
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.media.ExifInterface
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import java.io.ByteArrayOutputStream
import java.util.Locale
import kotlin.math.max

internal const val MAX_FOLDER_SCAN_FILES = 100
private const val MAX_TEXT_BYTES = 64 * 1024
private const val MAX_METADATA_BYTES = 4 * 1024 * 1024

internal data class ImageAnalysisPayload(
    val preview: Bitmap?,
    val result: ClassificationResult,
    val errorMessage: String? = null
)

private data class FolderDocument(
    val name: String,
    val mimeType: String?,
    val uri: Uri
)

internal fun scanFolder(context: Context, treeUri: Uri): List<BatchScanItem> {
    return runCatching {
        listFolderChildren(context.contentResolver, treeUri)
            .take(MAX_FOLDER_SCAN_FILES)
            .map { document -> analyzeFolderDocument(context, document) }
            .let(BatchScan::sort)
    }.getOrElse { error ->
        listOf(
            BatchScanItem(
                name = "선택한 폴더",
                kind = BatchItemKind.UNSUPPORTED,
                status = BatchItemStatus.FAILED,
                errorMessage = error.message ?: "폴더를 읽지 못했습니다."
            )
        )
    }
}

private fun listFolderChildren(resolver: ContentResolver, treeUri: Uri): List<FolderDocument> {
    val treeDocumentId = DocumentsContract.getTreeDocumentId(treeUri)
    val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, treeDocumentId)
    val projection = arrayOf(
        Document.COLUMN_DOCUMENT_ID,
        Document.COLUMN_DISPLAY_NAME,
        Document.COLUMN_MIME_TYPE
    )
    val documents = mutableListOf<FolderDocument>()
    resolver.query(childrenUri, projection, null, null, null)?.use { cursor ->
        while (cursor.moveToNext() && documents.size < MAX_FOLDER_SCAN_FILES) {
            val documentId = cursor.optionalString(Document.COLUMN_DOCUMENT_ID) ?: continue
            val name = cursor.optionalString(Document.COLUMN_DISPLAY_NAME) ?: documentId
            val mimeType = cursor.optionalString(Document.COLUMN_MIME_TYPE)
            val documentUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, documentId)
            documents += FolderDocument(name = name, mimeType = mimeType, uri = documentUri)
        }
    }
    return documents
}

private fun analyzeFolderDocument(context: Context, document: FolderDocument): BatchScanItem {
    val kind = kindFor(document.name, document.mimeType)
    return when (kind) {
        BatchItemKind.TEXT -> runCatching {
            val text = readBoundedText(context, document.uri)
            BatchScanItem(
                name = document.name,
                kind = kind,
                status = BatchItemStatus.ANALYZED,
                result = DeepfakeClassifier.analyzeText(text)
            )
        }.getOrElse { error -> failedItem(document.name, kind, error) }

        BatchItemKind.IMAGE -> {
            val payload = loadImageAnalysisPayload(context, document.uri, includePreview = false)
            BatchScanItem(
                name = document.name,
                kind = kind,
                status = if (payload.errorMessage == null) BatchItemStatus.ANALYZED else BatchItemStatus.FAILED,
                result = payload.result,
                errorMessage = payload.errorMessage
            )
        }

        BatchItemKind.UNSUPPORTED -> BatchScanItem(
            name = document.name,
            kind = kind,
            status = BatchItemStatus.UNSUPPORTED,
            errorMessage = "지원 형식은 jpg, jpeg, png, webp, txt, md 입니다."
        )
    }
}

private fun failedItem(name: String, kind: BatchItemKind, error: Throwable): BatchScanItem {
    return BatchScanItem(
        name = name,
        kind = kind,
        status = BatchItemStatus.FAILED,
        errorMessage = error.message ?: "파일을 읽지 못했습니다."
    )
}

private fun kindFor(name: String, mimeType: String?): BatchItemKind {
    val extension = name.substringAfterLast('.', missingDelimiterValue = "").lowercase(Locale.ROOT)
    val normalizedMime = mimeType?.lowercase(Locale.ROOT).orEmpty()
    return when {
        normalizedMime == Document.MIME_TYPE_DIR -> BatchItemKind.UNSUPPORTED
        extension in setOf("jpg", "jpeg", "png", "webp") ||
            normalizedMime in setOf("image/jpeg", "image/png", "image/webp") -> BatchItemKind.IMAGE
        extension in setOf("txt", "md") ||
            normalizedMime in setOf("text/plain", "text/markdown", "text/x-markdown") -> BatchItemKind.TEXT
        else -> BatchItemKind.UNSUPPORTED
    }
}

internal fun loadImageAnalysisPayload(context: Context, uri: Uri, includePreview: Boolean): ImageAnalysisPayload {
    return runCatching {
        var sourceWidth = 0
        var sourceHeight = 0
        val source = ImageDecoder.createSource(context.contentResolver, uri)
        val decoded = ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
            sourceWidth = info.size.width
            sourceHeight = info.size.height
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            val targetMax = if (includePreview) 720 else 320
            val largest = max(info.size.width, info.size.height)
            val sample = (largest / targetMax).coerceAtLeast(1)
            decoder.setTargetSize(
                (info.size.width / sample).coerceAtLeast(1),
                (info.size.height / sample).coerceAtLeast(1)
            )
        }.ensureArgb8888()
        val analysisBitmap = decoded.scaledForAnalysis(maxSide = 160)
        val pixels = IntArray(analysisBitmap.width * analysisBitmap.height)
        analysisBitmap.getPixels(
            pixels,
            0,
            analysisBitmap.width,
            0,
            0,
            analysisBitmap.width,
            analysisBitmap.height
        )
        val metadata = readMetadata(context, uri) + mapOf(
            "source_width" to sourceWidth.toString(),
            "source_height" to sourceHeight.toString()
        )
        val result = DeepfakeClassifier.analyzeImage(
            ImageSample(
                width = analysisBitmap.width,
                height = analysisBitmap.height,
                pixels = pixels,
                sourceWidth = sourceWidth,
                sourceHeight = sourceHeight,
                metadata = metadata
            )
        )
        ImageAnalysisPayload(preview = if (includePreview) decoded else null, result = result)
    }.getOrElse { error ->
        ImageAnalysisPayload(
            preview = null,
            result = unreadableImageResult(error),
            errorMessage = error.message ?: "지원하지 않는 이미지이거나 접근 권한이 없습니다."
        )
    }
}

private fun unreadableImageResult(error: Throwable): ClassificationResult {
    return ClassificationResult(
        score = 0,
        band = RiskBand.UNKNOWN,
        verdict = "사진을 읽지 못했습니다.",
        signals = emptyList(),
        limitations = listOf(error.message ?: "지원하지 않는 이미지이거나 접근 권한이 없습니다."),
        sourceGuess = SourceGuess.unknown("이미지를 열지 못해 출처 단서를 확인하지 못했습니다."),
        nextChecks = listOf("파일 형식과 접근 권한을 확인한 뒤 원본 파일로 다시 시도하세요.")
    )
}

private fun Bitmap.ensureArgb8888(): Bitmap {
    return if (config == Bitmap.Config.ARGB_8888) this else copy(Bitmap.Config.ARGB_8888, false)
}

private fun Bitmap.scaledForAnalysis(maxSide: Int): Bitmap {
    val largest = max(width, height)
    if (largest <= maxSide) return this
    val scale = maxSide.toDouble() / largest.toDouble()
    val targetWidth = (width * scale).toInt().coerceAtLeast(1)
    val targetHeight = (height * scale).toInt().coerceAtLeast(1)
    return Bitmap.createScaledBitmap(this, targetWidth, targetHeight, true).ensureArgb8888()
}

private fun readMetadata(context: Context, uri: Uri): Map<String, String> {
    return readExifMetadata(context, uri) + readPngMetadata(context, uri)
}

private fun readExifMetadata(context: Context, uri: Uri): Map<String, String> {
    val tags = listOf(
        "Software",
        "Make",
        "Model",
        "ImageDescription",
        "Artist",
        "DateTime",
        "UserComment"
    )
    return runCatching {
        context.contentResolver.openInputStream(uri)?.use { stream ->
            val exif = ExifInterface(stream)
            tags.mapNotNull { tag ->
                exif.getAttribute(tag)?.takeIf { it.isNotBlank() }?.let { value -> "exif.$tag" to value }
            }.toMap()
        } ?: emptyMap()
    }.getOrDefault(emptyMap())
}

private fun readPngMetadata(context: Context, uri: Uri): Map<String, String> {
    return runCatching {
        context.contentResolver.openInputStream(uri)?.use { stream ->
            PngMetadataReader.read(stream.readLimitedBytes(MAX_METADATA_BYTES))
        } ?: emptyMap()
    }.getOrDefault(emptyMap())
}

private fun readBoundedText(context: Context, uri: Uri): String {
    val stream = context.contentResolver.openInputStream(uri)
        ?: throw IllegalStateException("텍스트 파일 스트림을 열지 못했습니다.")
    return stream.use {
        stream.readLimitedBytes(MAX_TEXT_BYTES).toString(Charsets.UTF_8)
    }
}

private fun java.io.InputStream.readLimitedBytes(maxBytes: Int): ByteArray {
    val output = ByteArrayOutputStream()
    val buffer = ByteArray(4096)
    while (output.size() < maxBytes) {
        val allowed = minOf(buffer.size, maxBytes - output.size())
        val read = read(buffer, 0, allowed)
        if (read < 0) break
        output.write(buffer, 0, read)
    }
    return output.toByteArray()
}

private fun Cursor.optionalString(column: String): String? {
    val index = getColumnIndex(column)
    return if (index >= 0 && !isNull(index)) getString(index) else null
}
