package com.shinyoohag.deepfakeclassifier

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.Locale
import java.util.zip.InflaterInputStream

object PngMetadataReader {
    private val signature = byteArrayOf(
        0x89.toByte(),
        0x50,
        0x4e,
        0x47,
        0x0d,
        0x0a,
        0x1a,
        0x0a
    )
    private const val MAX_CHUNK_BYTES = 2 * 1024 * 1024

    fun read(bytes: ByteArray): Map<String, String> {
        if (!bytes.startsWith(signature)) return emptyMap()
        val metadata = linkedMapOf<String, String>()
        var offset = signature.size

        while (offset + 12 <= bytes.size) {
            val length = bytes.readInt(offset)
            val typeStart = offset + 4
            val dataStart = offset + 8
            val dataEnd = dataStart + length
            val nextOffset = dataEnd + 4
            if (length < 0 || length > MAX_CHUNK_BYTES || dataEnd > bytes.size || nextOffset > bytes.size) break

            val type = bytes.decodeText(typeStart, typeStart + 4, Charsets.ISO_8859_1)
            val data = bytes.copyOfRange(dataStart, dataEnd)
            when (type) {
                "tEXt" -> parseTextChunk(data)?.let { (key, value) -> metadata[metadataKey(key)] = value }
                "zTXt" -> parseCompressedTextChunk(data)?.let { (key, value) -> metadata[metadataKey(key)] = value }
                "iTXt" -> parseInternationalTextChunk(data)?.let { (key, value) -> metadata[metadataKey(key)] = value }
                "IEND" -> return metadata
            }
            offset = nextOffset
        }
        return metadata
    }

    private fun parseTextChunk(data: ByteArray): Pair<String, String>? {
        val separator = data.indexOf(0)
        if (separator <= 0) return null
        val key = data.decodeText(0, separator, Charsets.ISO_8859_1)
        val value = data.decodeText(separator + 1, data.size, Charsets.ISO_8859_1)
        return cleanPair(key, value)
    }

    private fun parseCompressedTextChunk(data: ByteArray): Pair<String, String>? {
        val separator = data.indexOf(0)
        if (separator <= 0 || separator + 2 > data.size) return null
        val compressionMethod = data[separator + 1].toInt()
        if (compressionMethod != 0) return null
        val key = data.decodeText(0, separator, Charsets.ISO_8859_1)
        val compressed = data.copyOfRange(separator + 2, data.size)
        val value = inflate(compressed).decodeToString()
        return cleanPair(key, value)
    }

    private fun parseInternationalTextChunk(data: ByteArray): Pair<String, String>? {
        val keywordEnd = data.indexOf(0)
        if (keywordEnd <= 0 || keywordEnd + 3 > data.size) return null
        val compressionFlag = data[keywordEnd + 1].toInt()
        val compressionMethod = data[keywordEnd + 2].toInt()
        var cursor = keywordEnd + 3
        val languageEnd = data.indexOf(0, startIndex = cursor)
        if (languageEnd < 0) return null
        cursor = languageEnd + 1
        val translatedEnd = data.indexOf(0, startIndex = cursor)
        if (translatedEnd < 0) return null
        cursor = translatedEnd + 1

        val key = data.decodeText(0, keywordEnd, Charsets.ISO_8859_1)
        val textBytes = data.copyOfRange(cursor, data.size)
        val value = when {
            compressionFlag == 0 -> textBytes.decodeToString()
            compressionFlag == 1 && compressionMethod == 0 -> inflate(textBytes).decodeToString()
            else -> return null
        }
        return cleanPair(key, value)
    }

    private fun inflate(data: ByteArray): ByteArray {
        return runCatching {
            InflaterInputStream(ByteArrayInputStream(data)).use { input ->
                val output = ByteArrayOutputStream()
                val buffer = ByteArray(4096)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    output.write(buffer, 0, read)
                    if (output.size() > MAX_CHUNK_BYTES) break
                }
                output.toByteArray()
            }
        }.getOrDefault(ByteArray(0))
    }

    private fun cleanPair(key: String, value: String): Pair<String, String>? {
        val cleanKey = key.trim()
        val cleanValue = value.trim()
        if (cleanKey.isBlank() || cleanValue.isBlank()) return null
        return cleanKey to cleanValue
    }

    private fun metadataKey(key: String): String {
        return "png.${key.trim().lowercase(Locale.ROOT).replace(Regex("\\s+"), "_")}"
    }

    private fun ByteArray.startsWith(prefix: ByteArray): Boolean {
        if (size < prefix.size) return false
        return prefix.indices.all { this[it] == prefix[it] }
    }

    private fun ByteArray.readInt(offset: Int): Int {
        return ((this[offset].toInt() and 0xff) shl 24) or
            ((this[offset + 1].toInt() and 0xff) shl 16) or
            ((this[offset + 2].toInt() and 0xff) shl 8) or
            (this[offset + 3].toInt() and 0xff)
    }

    private fun ByteArray.indexOf(value: Int, startIndex: Int = 0): Int {
        for (index in startIndex until size) {
            if (this[index].toInt() == value) return index
        }
        return -1
    }

    private fun ByteArray.decodeText(start: Int, end: Int, charset: java.nio.charset.Charset): String {
        if (start >= end || start < 0 || end > size) return ""
        return copyOfRange(start, end).toString(charset)
    }
}
